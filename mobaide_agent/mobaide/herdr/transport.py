"""Byte transports to a herdr server: its local socket and its bridge process.

Both hand out an ``asyncio.StreamReader``. Where the event loop cannot do the
I/O itself, a thread feeds the reader: on Windows the agent runs a
``SelectorEventLoop``, which can open neither a named pipe nor a subprocess.
The bridge process uses threads on every platform, so one code path runs
everywhere. The threads and the pool are private: the default executor can be
full of blocked PTY reads.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import IO, Any

from mobaide.core.cli_launcher import WINDOWS_NO_WINDOW

logger = logging.getLogger(__name__)

# The largest single message a herdr product sends: a full terminal frame is
# capped at 2 MB upstream and travels base64-encoded in one JSON line.
STREAM_LIMIT_BYTES = 8 * 1024 * 1024

_READ_CHUNK_BYTES = 64 * 1024
_EXIT_GRACE_SECONDS = 1.0

_IO_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="herdr-io")


def _feed_from_thread(
    source: IO[bytes], reader: asyncio.StreamReader, loop: asyncio.AbstractEventLoop
) -> None:
    """Copy ``source`` into ``reader`` until EOF or an error, then feed EOF."""

    def post(fn, *args) -> None:
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass  # the loop is closed: the agent is shutting down

    # ``read1`` returns what is there; ``read`` on a buffered pipe would wait
    # for a full chunk and hold back a small frame.
    read = getattr(source, "read1", source.read)
    try:
        while True:
            chunk = read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            post(reader.feed_data, chunk)
    except (OSError, ValueError):
        pass
    post(reader.feed_eof)


def _start_feeder(
    source: IO[bytes], loop: asyncio.AbstractEventLoop, name: str
) -> asyncio.StreamReader:
    reader = asyncio.StreamReader(limit=STREAM_LIMIT_BYTES, loop=loop)
    threading.Thread(
        target=_feed_from_thread, args=(source, reader, loop), name=name, daemon=True
    ).start()
    return reader


class LocalStream:
    """One connection to a herdr local socket."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        *,
        writer: asyncio.StreamWriter | None = None,
        pipe: IO[bytes] | None = None,
    ) -> None:
        self.reader = reader
        self._writer = writer
        self._pipe = pipe

    async def write(self, data: bytes) -> None:
        if self._writer is not None:
            self._writer.write(data)
            await self._writer.drain()
            return
        assert self._pipe is not None
        await asyncio.get_running_loop().run_in_executor(
            _IO_POOL, _write_all, self._pipe, data
        )

    async def aclose(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
            except asyncio.TimeoutError:
                # Unsent data to a peer that does not read: drop it, or the
                # socket stays open for as long as that peer lives.
                self._writer.transport.abort()
            except (OSError, RuntimeError):
                pass
            return
        if self._pipe is not None:
            pipe, self._pipe = self._pipe, None
            await asyncio.get_running_loop().run_in_executor(
                _IO_POOL, _close_pipe, pipe
            )


def _write_all(target: IO[bytes], data: bytes) -> None:
    target.write(data)
    target.flush()


def _close_pipe(pipe: IO[bytes]) -> None:
    try:
        pipe.close()
    except OSError:
        pass


# Not in ``_winapi``. The two flags cap what the pipe's server may do with this
# process's identity: it can read who connected and cannot act as that user.
_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000


class _OverlappedPipe:
    """A Windows named pipe that one thread reads while another writes.

    A handle opened without ``FILE_FLAG_OVERLAPPED`` serializes its I/O: the
    feeder thread's blocking read would hold back every write, and the reply
    that read waits for needs that write. Each call here is one overlapped
    operation that the calling thread waits for, as
    ``multiprocessing.connection.PipeConnection`` does it. Windows only; not
    yet run on a Windows computer.
    """

    def __init__(self, name: str) -> None:
        import _winapi

        self._api: Any = _winapi
        self._handle = _winapi.CreateFile(
            name,
            _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
            0,
            _winapi.NULL,
            _winapi.OPEN_EXISTING,
            _winapi.FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
            _winapi.NULL,
        )
        self._close_lock = threading.Lock()
        self._closed = False

    def _finish(self, overlapped: Any, pending: int) -> tuple[int, int]:
        api = self._api
        try:
            if pending == api.ERROR_IO_PENDING:
                api.WaitForMultipleObjects([overlapped.event], False, api.INFINITE)
        except BaseException:
            overlapped.cancel()
            raise
        return overlapped.GetOverlappedResult(True)

    def read1(self, size: int) -> bytes:
        api = self._api
        try:
            overlapped, pending = api.ReadFile(self._handle, size, overlapped=True)
            _, error = self._finish(overlapped, pending)
        except OSError as exc:
            if getattr(exc, "winerror", None) == api.ERROR_BROKEN_PIPE:
                return b""  # the server closed its end
            raise
        if error not in (0, api.ERROR_MORE_DATA):
            return b""
        return bytes(overlapped.getbuffer() or b"")

    def write(self, data: bytes) -> int:
        api = self._api
        view = memoryview(data)
        sent = 0
        while sent < len(view):
            overlapped, pending = api.WriteFile(self._handle, view[sent:], overlapped=True)
            written, _ = self._finish(overlapped, pending)
            if written <= 0:
                raise BrokenPipeError("the pipe took no data")
            sent += written
        return sent

    def flush(self) -> None:
        return None

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        try:
            import ctypes

            # Ends the feeder's pending read, so its thread leaves.
            ctypes.windll.kernel32.CancelIoEx(ctypes.c_void_p(self._handle), None)
        except (OSError, ValueError, AttributeError):
            pass
        try:
            self._api.CloseHandle(self._handle)
        except OSError:
            pass


def windows_pipe_name(path: str) -> str:
    """The named pipe behind a herdr socket path: herdr uses the path string
    itself as the pipe name."""
    prefix = "\\\\.\\pipe\\"
    return path if path.startswith(prefix) else prefix + path


async def open_local_stream(path: str) -> LocalStream:
    """Connect to a herdr local socket. Raises ``OSError`` when nothing
    listens there."""
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        reader, writer = await asyncio.open_unix_connection(
            path, limit=STREAM_LIMIT_BYTES
        )
        return LocalStream(reader, writer=writer)
    pipe: Any = await loop.run_in_executor(
        _IO_POOL, _OverlappedPipe, windows_pipe_name(path)
    )
    return LocalStream(_start_feeder(pipe, loop, "herdr-pipe"), pipe=pipe)


class PipedProcess:
    """A child process with its stdout as a stream and a queued stdin."""

    def __init__(self, proc: Any, stdout: asyncio.StreamReader) -> None:
        self._proc = proc
        self.stdout = stdout
        self._stderr_tail: list[str] = []
        # Writes to stdin that have not returned; see ``_terminate``.
        self._writes = 0
        self._writes_lock = threading.Lock()

    @property
    def pid(self) -> int:
        return self._proc.pid

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail[-5:])

    async def write_stdin(self, data: bytes) -> None:
        stdin = self._proc.stdin
        if stdin is None or stdin.closed:
            raise BrokenPipeError("stdin is closed")
        await asyncio.get_running_loop().run_in_executor(
            _IO_POOL, self._write_stdin, stdin, data
        )

    def _write_stdin(self, stdin: IO[bytes], data: bytes) -> None:
        with self._writes_lock:
            self._writes += 1
        try:
            _write_all(stdin, data)
        finally:
            with self._writes_lock:
                self._writes -= 1

    async def terminate(self) -> None:
        """Close stdin, give the process a moment to leave, then kill it."""
        await asyncio.get_running_loop().run_in_executor(_IO_POOL, self._terminate)

    def _terminate(self) -> None:
        proc = self._proc
        with self._writes_lock:
            write_blocked = self._writes > 0
        if write_blocked:
            # A write that the process does not read holds the lock of the
            # stdin object, and ``close`` below would wait for that lock for
            # ever. The kill ends the write with a broken pipe.
            try:
                proc.kill()
            except OSError:
                pass
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=_EXIT_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
            proc.wait(timeout=3.0)
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("herdr.bridge_unreaped pid=%s", proc.pid)

    def _drain_stderr(self) -> None:
        stderr = self._proc.stderr
        if stderr is None:
            return
        try:
            for raw in stderr:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._stderr_tail.append(line[:300])
                    del self._stderr_tail[:-5]
        except (OSError, ValueError):
            pass


async def spawn_piped(
    argv: Sequence[str], *, env: Mapping[str, str] | None = None
) -> PipedProcess:
    """Start ``argv`` with piped stdio. Raises ``OSError`` when it cannot start."""
    loop = asyncio.get_running_loop()
    kwargs: dict[str, Any] = {
        "env": dict(env) if env is not None else None,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "creationflags": WINDOWS_NO_WINDOW,
    }
    popen = loop.run_in_executor(_IO_POOL, partial(subprocess.Popen, list(argv), **kwargs))
    try:
        proc = await asyncio.shield(popen)
    except asyncio.CancelledError:
        popen.add_done_callback(_kill_unowned)
        raise
    assert proc.stdout is not None
    process = PipedProcess(proc, _start_feeder(proc.stdout, loop, "herdr-bridge-out"))
    threading.Thread(
        target=process._drain_stderr, name="herdr-bridge-err", daemon=True
    ).start()
    return process


def _kill_unowned(fut: asyncio.Future[Any]) -> None:
    if fut.cancelled() or fut.exception() is not None:
        return
    proc = fut.result()
    try:
        proc.kill()
    except OSError:
        pass


async def run_text(argv: Sequence[str], *, timeout: float = 5.0) -> str | None:
    """Run a short command and return its stdout, or ``None`` when it fails."""

    def call() -> str | None:
        try:
            done = subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=WINDOWS_NO_WINDOW,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        return done.stdout.decode("utf-8", errors="replace")

    return await asyncio.get_running_loop().run_in_executor(_IO_POOL, call)


async def in_thread(fn, *args):
    """Run a short blocking call in the herdr pool."""
    return await asyncio.get_running_loop().run_in_executor(_IO_POOL, fn, *args)


def is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)
