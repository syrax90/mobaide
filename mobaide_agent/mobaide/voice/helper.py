"""Agent-side supervisor of the STT worker process.

The worker is spawned on first use, loads the model once, transcribes one
segment at a time and is stopped after ``HELPER_IDLE_SECONDS`` without work,
which returns all of its memory to the OS. It is also recycled after
``HELPER_MAX_LIFETIME_SECONDS`` or ``HELPER_MAX_REQUESTS`` to bound slow leaks
in the native runtime, killed when a request outlives its timeout, and
respawned on the next request after a crash.

Nothing here blocks the event loop: the process is spawned, written to and
killed from worker threads, and two reader threads hand replies back with
``call_soon_threadsafe`` (``asyncio`` subprocesses are unavailable under the
``WindowsSelectorEventLoop`` the agent runs on). Requests carry ids, so the
late reply of a request that was given up on is dropped instead of being
taken for the next one.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mobaide
from mobaide.core.git_exec import _kill_tree

from .ipc import FrameError, encode_frame, read_frame

logger = logging.getLogger(__name__)

HELPER_IDLE_SECONDS = 180.0
HELPER_MAX_LIFETIME_SECONDS = 3600.0
HELPER_MAX_REQUESTS = 500
LOAD_TIMEOUT_SECONDS = 120.0
MIN_TRANSCRIBE_TIMEOUT_SECONDS = 30.0
TRANSCRIBE_TIMEOUT_FACTOR = 4.0
NUM_THREADS = 2
_STOP_GRACE_SECONDS = 3.0
_REAP_SECONDS = 5.0
_STDERR_TAIL_LINES = 20

_WINDOWS_FLAGS = (
    subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    if sys.platform == "win32"
    else 0
)
_SECRET_ENV = re.compile(r"SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY", re.IGNORECASE)


class HelperError(Exception):
    """``code``: ``helper_unavailable`` | ``helper_crashed`` | ``helper_timeout``
    | ``helper_stopped`` | ``load_failed`` | ``transcribe_failed``."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LoadSpec:
    """What the worker must have loaded; a different ``key`` reloads it."""

    key: str
    engine: str
    model_dir: Path
    runtime_dir: Path | None
    files: dict[str, str] = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class TranscribeResult:
    text: str
    decode_ms: int
    load_ms: int | None
    peak_rss_bytes: int | None


@dataclass
class _Worker:
    proc: subprocess.Popen[bytes]
    started_at: float
    pending: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    stderr_tail: collections.deque[str] = field(
        default_factory=lambda: collections.deque(maxlen=_STDERR_TAIL_LINES)
    )
    loaded_key: str | None = None
    requests: int = 0
    exited: bool = False

    def tail(self) -> str:
        return "\n".join(self.stderr_tail)[-1000:]


def _package_parent() -> str:
    return str(Path(mobaide.__file__).resolve().parents[1])


def _worker_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    # The worker must import this very ``mobaide`` package and nothing from its
    # working directory: ``-m`` would otherwise put the cwd first on sys.path,
    # so a planted ``mobaide/`` there would run in place of the worker (``-P``
    # and PYTHONSAFEPATH both close that).
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = _package_parent() + (os.pathsep + existing if existing else "")
    env["PYTHONSAFEPATH"] = "1"
    return env


def _post(loop: asyncio.AbstractEventLoop, fn: Any, *args: Any) -> None:
    try:
        loop.call_soon_threadsafe(fn, *args)
    except RuntimeError:
        pass


def _send_shutdown(proc: subprocess.Popen[bytes]) -> None:
    try:
        assert proc.stdin is not None
        proc.stdin.write(encode_frame({"op": "shutdown"}))
        proc.stdin.flush()
    except (OSError, ValueError):
        pass


def _shutdown_process(proc: subprocess.Popen[bytes], graceful: bool) -> None:
    # The polite request is written from its own thread: a wedged worker with
    # a full pipe (or a writer thread of an abandoned request still holding
    # the stdin lock) would block it forever, and the kill must still follow
    # the grace period.
    sender: threading.Thread | None = None
    if graceful and proc.poll() is None and proc.stdin is not None:
        sender = threading.Thread(
            target=_send_shutdown, args=(proc,), name="voice-helper-shutdown", daemon=True
        )
        sender.start()
        try:
            proc.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    if proc.poll() is None:
        _kill_tree(proc)
    try:
        proc.wait(timeout=_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("voice.helper_reap_timeout pid=%s", proc.pid)
    if sender is not None:
        sender.join(timeout=1.0)
    if proc.stdin is not None and (sender is None or not sender.is_alive()):
        try:
            proc.stdin.close()
        except OSError:
            pass


class SttHelper:
    def __init__(
        self,
        *,
        python: str | None = None,
        idle_seconds: float = HELPER_IDLE_SECONDS,
        max_lifetime_seconds: float = HELPER_MAX_LIFETIME_SECONDS,
        max_requests: int = HELPER_MAX_REQUESTS,
    ) -> None:
        self._python = python if python is not None else sys.executable
        self._idle_seconds = idle_seconds
        self._max_lifetime = max_lifetime_seconds
        self._max_requests = max_requests
        self._lock = asyncio.Lock()
        self._worker: _Worker | None = None
        self._outstanding = 0
        self._next_id = 0
        self._idle_handle: asyncio.TimerHandle | None = None
        self._background: set[asyncio.Task[Any]] = set()

    @property
    def busy(self) -> bool:
        return self._outstanding > 0

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.exited

    @property
    def pid(self) -> int | None:
        return self._worker.proc.pid if self._worker is not None else None

    async def preload(self, spec: LoadSpec) -> int | None:
        """Load the model ahead of the first segment so the load overlaps
        the user's speech; returns the load time when this call loaded it."""
        self._outstanding += 1
        self._cancel_idle()
        try:
            async with self._lock:
                return await self._ensure_loaded(spec)
        finally:
            self._outstanding -= 1
            self._after_request()

    async def transcribe(self, spec: LoadSpec, pcm16: bytes, sample_rate: int) -> TranscribeResult:
        self._outstanding += 1
        self._cancel_idle()
        try:
            async with self._lock:
                load_ms = await self._ensure_loaded(spec)
                worker = self._worker
                if worker is None:
                    raise HelperError("helper_crashed", "The speech worker exited.")
                seconds = len(pcm16) / 2 / max(sample_rate, 1)
                timeout = max(MIN_TRANSCRIBE_TIMEOUT_SECONDS, seconds * TRANSCRIBE_TIMEOUT_FACTOR)
                reply = await self._request(
                    worker, {"op": "transcribe", "sample_rate": sample_rate}, pcm16, timeout
                )
                worker.requests += 1
                if not reply.get("ok"):
                    error = reply.get("error") or {}
                    raise HelperError(
                        str(error.get("code") or "transcribe_failed"),
                        str(error.get("message") or ""),
                    )
                return TranscribeResult(
                    text=str(reply.get("text") or ""),
                    decode_ms=int(reply.get("decode_ms") or 0),
                    load_ms=load_ms,
                    peak_rss_bytes=reply.get("peak_rss_bytes"),
                )
        finally:
            self._outstanding -= 1
            self._after_request()

    async def stop(self) -> None:
        """Stop the worker now; an in-flight request fails with
        ``helper_stopped``."""
        self._cancel_idle()
        worker = self._worker
        if worker is not None:
            await self._terminate(worker, graceful=not self.busy, reason="stop")
        for task in list(self._background):
            task.cancel()

    async def _ensure_loaded(self, spec: LoadSpec) -> int | None:
        worker = self._worker
        if worker is not None and (worker.exited or self._expired(worker)):
            await self._terminate(worker, graceful=not worker.exited, reason="recycle")
            worker = None
        if worker is not None and worker.loaded_key not in (None, spec.key):
            await self._terminate(worker, graceful=True, reason="reload")
            worker = None
        if worker is None:
            worker = await self._spawn()
        if worker.loaded_key == spec.key:
            return None
        reply = await self._request(
            worker,
            {
                "op": "load",
                "engine": spec.engine,
                "model_dir": str(spec.model_dir),
                "runtime_dir": str(spec.runtime_dir) if spec.runtime_dir else None,
                "files": spec.files,
                "num_threads": NUM_THREADS,
            },
            b"",
            LOAD_TIMEOUT_SECONDS,
        )
        if not reply.get("ok"):
            error = reply.get("error") or {}
            await self._terminate(worker, graceful=True, reason="load_failed")
            raise HelperError("load_failed", str(error.get("message") or ""))
        worker.loaded_key = spec.key
        load_ms = int(reply.get("load_ms") or 0)
        logger.info(
            "voice.helper_loaded pid=%s worker_pid=%s load_ms=%d peak_rss_bytes=%s",
            worker.proc.pid, reply.get("pid"), load_ms, reply.get("peak_rss_bytes"),
        )
        return load_ms

    def _expired(self, worker: _Worker) -> bool:
        return (
            worker.requests >= self._max_requests
            or time.monotonic() - worker.started_at >= self._max_lifetime
        )

    async def _spawn(self) -> _Worker:
        if not self._python:
            raise HelperError("helper_unavailable", "No Python interpreter to start the worker.")
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": _worker_env(),
            "close_fds": True,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = _WINDOWS_FLAGS
        else:
            kwargs["start_new_session"] = True
        argv = [self._python, "-P", "-m", "mobaide.voice.worker"]
        kwargs["cwd"] = _package_parent()
        try:
            proc: subprocess.Popen[bytes] = await asyncio.to_thread(
                lambda: subprocess.Popen(argv, **kwargs)
            )
        except OSError as exc:
            raise HelperError("helper_unavailable", str(exc)) from exc
        worker = _Worker(proc=proc, started_at=time.monotonic())
        loop = asyncio.get_running_loop()
        threading.Thread(
            target=self._read_replies, args=(worker, loop), name="voice-helper-out", daemon=True
        ).start()
        threading.Thread(
            target=self._read_stderr, args=(worker,), name="voice-helper-err", daemon=True
        ).start()
        self._worker = worker
        logger.info("voice.helper_started pid=%s", proc.pid)
        return worker

    async def _request(
        self, worker: _Worker, header: dict[str, Any], payload: bytes, timeout: float
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        worker.pending[request_id] = future
        data = encode_frame({**header, "id": request_id}, payload)
        write = asyncio.ensure_future(asyncio.to_thread(self._write, worker, data))
        write.add_done_callback(consume_exception)
        try:
            async with asyncio.timeout(timeout):
                await asyncio.shield(write)
                return await future
        except TimeoutError as exc:
            logger.warning(
                "voice.helper_timeout pid=%s op=%s timeout_s=%.0f",
                worker.proc.pid, header.get("op"), timeout,
            )
            await self._terminate(worker, graceful=False, reason="timeout")
            raise HelperError(
                "helper_timeout", "Speech recognition did not answer in time."
            ) from exc
        except OSError as exc:
            await self._terminate(worker, graceful=False, reason="crash")
            raise HelperError("helper_crashed", worker.tail() or str(exc)) from exc
        except HelperError:
            if worker.exited:
                await self._terminate(worker, graceful=False, reason="crash")
            raise
        finally:
            worker.pending.pop(request_id, None)

    @staticmethod
    def _write(worker: _Worker, data: bytes) -> None:
        stdin = worker.proc.stdin
        if stdin is None:
            raise BrokenPipeError("worker stdin closed")
        stdin.write(data)
        stdin.flush()

    def _read_replies(self, worker: _Worker, loop: asyncio.AbstractEventLoop) -> None:
        stdout = worker.proc.stdout
        try:
            while stdout is not None:
                frame = read_frame(stdout)
                if frame is None:
                    break
                _post(loop, self._on_reply, worker, frame[0])
        except (FrameError, OSError, ValueError) as exc:
            worker.stderr_tail.append(f"reply stream broken: {exc}")
        finally:
            if stdout is not None:
                try:
                    stdout.close()
                except OSError:
                    pass
            _post(loop, self._on_exit, worker)

    @staticmethod
    def _read_stderr(worker: _Worker) -> None:
        stderr = worker.proc.stderr
        if stderr is None:
            return
        try:
            for raw in iter(stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()[:300]
                if line:
                    worker.stderr_tail.append(line)
                    logger.debug("voice.helper_stderr pid=%s %s", worker.proc.pid, line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stderr.close()
            except OSError:
                pass

    def _on_reply(self, worker: _Worker, header: dict[str, Any]) -> None:
        request_id = header.get("id")
        future = worker.pending.pop(request_id, None) if isinstance(request_id, int) else None
        if future is not None and not future.done():
            future.set_result(header)

    def _on_exit(self, worker: _Worker) -> None:
        worker.exited = True
        error = HelperError("helper_crashed", worker.tail() or "The speech worker exited.")
        for future in worker.pending.values():
            if not future.done():
                future.set_exception(error)
        worker.pending.clear()
        if self._worker is worker:
            self._worker = None
            logger.warning("voice.helper_exited pid=%s", worker.proc.pid)
            self._spawn_background(self._terminate(worker, graceful=False, reason="exited"))

    async def _terminate(self, worker: _Worker, *, graceful: bool, reason: str) -> None:
        if self._worker is worker:
            self._worker = None
        stopped = HelperError("helper_stopped", "The speech worker was stopped.")
        for future in worker.pending.values():
            if not future.done():
                future.set_exception(stopped)
        worker.pending.clear()
        logger.info("voice.helper_stopping pid=%s reason=%s", worker.proc.pid, reason)
        await asyncio.to_thread(_shutdown_process, worker.proc, graceful)

    def _after_request(self) -> None:
        worker = self._worker
        if self._outstanding or worker is None:
            return
        if self._expired(worker):
            # Recycle while nothing is queued rather than on the next request.
            self._spawn_background(self._retire(worker))
        else:
            self._arm_idle()

    async def _retire(self, worker: _Worker) -> None:
        async with self._lock:
            if self._worker is worker and not self._outstanding:
                await self._terminate(worker, graceful=True, reason="recycle")

    def _arm_idle(self) -> None:
        self._cancel_idle()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(
            self._idle_seconds, lambda: self._spawn_background(self._idle_stop())
        )

    def _cancel_idle(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    async def _idle_stop(self) -> None:
        if self._outstanding:
            return
        async with self._lock:
            worker = self._worker
            if worker is None or self._outstanding:
                return
            await self._terminate(worker, graceful=True, reason="idle")

    def _spawn_background(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        task.add_done_callback(consume_exception)


def consume_exception(task: asyncio.Future[Any]) -> None:
    """Done-callback marking a fire-and-forget task's exception as seen."""
    if not task.cancelled():
        task.exception()
