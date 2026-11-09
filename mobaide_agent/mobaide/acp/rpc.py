"""Newline-delimited JSON-RPC 2.0 over a subprocess's stdio.

The transport is thread-based on purpose: on Windows the agent runs a
``SelectorEventLoop``, which has no ``asyncio`` subprocess support. One
thread reads stdout, one drains stderr and one writes stdin; every message
crosses into the event loop through ``call_soon_threadsafe``.
"""
from __future__ import annotations

import asyncio
import collections
import itertools
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import orjson

from mobaide.core.cli_launcher import WINDOWS_NO_WINDOW
from mobaide.core.git_exec import _kill_tree

logger = logging.getLogger(__name__)

# A single JSON-RPC line larger than this is dropped (and logged): a replayed
# session can carry big tool outputs, but nothing legitimate comes close.
MAX_LINE_BYTES = 32 * 1024 * 1024
STDERR_TAIL_LINES = 40

ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_AUTH_REQUIRED = -32000
ERR_NOT_FOUND = -32002
ERR_CANCELLED = -32800

# Spawning and killing processes block; they run here, not in the default
# executor, which running PTY sessions can fill with blocked reads.
_PROC_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="acp-proc")


def _kill_and_wait(proc: subprocess.Popen[bytes]) -> None:
    _kill_tree(proc)
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        logger.warning("acp.unowned_process_unreaped pid=%s", proc.pid)


def _kill_group(pid: int) -> None:
    if sys.platform == "win32":
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _reap_unowned(fut: asyncio.Future[subprocess.Popen[bytes]]) -> None:
    if fut.cancelled() or fut.exception() is not None:
        return
    _PROC_POOL.submit(_kill_and_wait, fut.result())


NotificationHandler = Callable[[str, Any], None]
RequestHandler = Callable[[str, Any], Awaitable[Any]]
ExitHandler = Callable[[int | None], None]


class AcpRpcError(Exception):
    """A JSON-RPC error object, received from or sent to the peer."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_wire(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


class AcpProcessError(RuntimeError):
    """The agent process could not start, or exited while a call was open."""


class AcpConnection:
    """One ACP agent subprocess and the JSON-RPC session over its stdio."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None,
        env: Mapping[str, str] | None,
        on_notification: NotificationHandler,
        on_request: RequestHandler,
        on_exit: ExitHandler | None = None,
        name: str = "acp",
    ) -> None:
        self._argv = list(argv)
        self._cwd = cwd
        self._env = dict(env) if env is not None else None
        self._on_notification = on_notification
        self._on_request = on_request
        self._on_exit = on_exit
        self._name = name
        self._proc: subprocess.Popen[bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._inbound_tasks: set[asyncio.Task[None]] = set()
        self._out_queue: queue.Queue[bytes | None] = queue.Queue()
        self._stderr_tail: collections.deque[str] = collections.deque(
            maxlen=STDERR_TAIL_LINES
        )
        self._closed = False
        self._exited = False
        self._returncode: int | None = None
        self._exit_event = asyncio.Event()
        self._close_task: asyncio.Future[None] | None = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def alive(self) -> bool:
        return self._proc is not None and not self._exited

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    async def start(self) -> None:
        """Spawn the process. Raises ``OSError`` when it cannot be spawned."""
        self._loop = asyncio.get_running_loop()
        popen_kwargs: dict[str, Any] = {
            "cwd": self._cwd,
            "env": self._env,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "creationflags": WINDOWS_NO_WINDOW,
        }
        if sys.platform != "win32":
            # Own process group, so stop() reaps the adapter and every CLI
            # it spawned in one kill.
            popen_kwargs["start_new_session"] = True
        self._exit_event = asyncio.Event()
        popen = self._loop.run_in_executor(
            _PROC_POOL, partial(subprocess.Popen, self._argv, **popen_kwargs)
        )
        try:
            self._proc = await asyncio.shield(popen)
        except asyncio.CancelledError:
            # Popen still finishes in the pool thread: kill what it starts.
            popen.add_done_callback(_reap_unowned)
            raise
        for target, label in (
            (self._read_stdout, "out"),
            (self._read_stderr, "err"),
            (self._write_stdin, "in"),
        ):
            threading.Thread(
                target=target, name=f"{self._name}-{label}", daemon=True
            ).start()

    async def request(
        self, method: str, params: Any, timeout: float | None = None
    ) -> Any:
        """Send a request and await its result.

        Raises :class:`AcpRpcError` for an error reply, and
        :class:`AcpProcessError` when the process is gone.
        """
        if self._exited or self._closed or self._loop is None:
            raise AcpProcessError(self._exit_message())
        req_id = next(self._ids)
        fut: asyncio.Future[Any] = self._loop.create_future()
        self._pending[req_id] = fut
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            if timeout is None:
                return await fut
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(req_id, None)

    def notify(self, method: str, params: Any) -> None:
        if self._exited or self._closed:
            return
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def close(self, grace: float = 2.0) -> None:
        """Close stdin, give the process ``grace`` seconds, then kill its tree.

        Every caller waits for the same close, and cancelling one caller does
        not stop it, so a process is never left half-closed.
        """
        if self._close_task is None:
            self._closed = True
            self._out_queue.put(None)
            self._close_task = asyncio.ensure_future(self._finish_close(grace))
        await asyncio.shield(self._close_task)

    async def _finish_close(self, grace: float) -> None:
        proc = self._proc
        if proc is None:
            return
        # The stdout thread reaps the process and sets the event on exit.
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(self._exit_event.wait(), grace)
        except TimeoutError:
            await loop.run_in_executor(_PROC_POOL, _kill_tree, proc)
            try:
                await asyncio.wait_for(self._exit_event.wait(), 3.0)
            except TimeoutError:
                logger.warning("acp.close.unreaped name=%s pid=%s", self._name, proc.pid)
        else:
            if proc.poll() is None:
                # stdout closed but the process lives on: kill it.
                await loop.run_in_executor(_PROC_POOL, _kill_tree, proc)
        for task in list(self._inbound_tasks):
            task.cancel()

    def _send(self, message: dict[str, Any]) -> None:
        self._out_queue.put(orjson.dumps(message) + b"\n")

    def _write_stdin(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        stdin = proc.stdin
        try:
            while True:
                data = self._out_queue.get()
                if data is None:
                    break
                stdin.write(data)
                stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                stdin.close()
            except OSError:
                pass

    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        stdout = proc.stdout
        try:
            while True:
                line = stdout.readline(MAX_LINE_BYTES)
                if not line:
                    break
                if not line.endswith(b"\n") and len(line) >= MAX_LINE_BYTES:
                    logger.warning("acp.line_too_long name=%s — dropped", self._name)
                    while True:
                        rest = stdout.readline(MAX_LINE_BYTES)
                        if not rest or rest.endswith(b"\n"):
                            break
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    message = orjson.loads(line)
                except orjson.JSONDecodeError:
                    logger.debug("acp.non_json_line name=%s line=%r", self._name, line[:200])
                    continue
                self._post(self._dispatch, message)
        except (OSError, ValueError):
            pass
        returncode: int | None
        try:
            returncode = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = None
        else:
            # Commands the agent ran in the background outlive it in its
            # group. Killed right at the reap: later, the id may be reused.
            _kill_group(proc.pid)
        self._post(self._handle_exit, returncode)

    def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            for raw in iter(proc.stderr.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    self._stderr_tail.append(text[:500])
                    logger.debug("acp.stderr name=%s %s", self._name, text[:500])
        except (OSError, ValueError):
            pass

    def _post(self, fn: Callable[..., None], *args: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass

    def _dispatch(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        method = message.get("method")
        msg_id = message.get("id")
        if method is None:
            if not isinstance(msg_id, int):
                return
            fut = self._pending.get(msg_id)
            if fut is None or fut.done():
                return
            error = message.get("error")
            if isinstance(error, dict):
                fut.set_exception(
                    AcpRpcError(
                        int(error.get("code", ERR_INTERNAL)),
                        str(error.get("message", "error")),
                        error.get("data"),
                    )
                )
            else:
                fut.set_result(message.get("result"))
            return
        params = message.get("params")
        if msg_id is None:
            try:
                self._on_notification(str(method), params)
            except Exception:
                logger.exception("acp.notification_handler_failed method=%s", method)
            return
        task = asyncio.ensure_future(self._serve_request(msg_id, str(method), params))
        self._inbound_tasks.add(task)
        task.add_done_callback(self._inbound_tasks.discard)

    async def _serve_request(self, msg_id: Any, method: str, params: Any) -> None:
        try:
            result = await self._on_request(method, params)
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except AcpRpcError as exc:
            reply = {"jsonrpc": "2.0", "id": msg_id, "error": exc.to_wire()}
        except asyncio.CancelledError:
            reply = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": ERR_CANCELLED, "message": "Request cancelled"},
            }
        except Exception as exc:
            logger.exception("acp.request_handler_failed method=%s", method)
            reply = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": ERR_INTERNAL, "message": str(exc) or "internal error"},
            }
        if not self._exited and not self._closed:
            self._send(reply)

    def _handle_exit(self, returncode: int | None) -> None:
        if self._exited:
            return
        self._exited = True
        self._returncode = returncode
        self._exit_event.set()
        message = self._exit_message()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(AcpProcessError(message))
        self._pending.clear()
        if self._on_exit is not None:
            try:
                self._on_exit(returncode)
            except Exception:
                logger.exception("acp.exit_handler_failed name=%s", self._name)

    def _exit_message(self) -> str:
        tail = self.stderr_tail()
        code = self._returncode
        base = (
            "The agent process exited"
            if code is None
            else f"The agent process exited with code {code}"
        )
        if tail:
            last = tail.splitlines()[-1]
            return f"{base}: {last}"
        return base
