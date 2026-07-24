from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
from typing import Protocol

logger = logging.getLogger(__name__)


def _terminal_env(env: dict[str, str] | None) -> dict[str, str]:
    """Build the spawn environment with TERM set for TUI rendering.

    COLUMNS/LINES are deliberately NOT exported: ncurses (and readline) prefer
    them over the live TIOCGWINSZ size, so a static value pins every TUI at the
    spawn dimensions and later client resizes are ignored. The PTY winsize —
    set before spawn and updated via ``resize()`` — is the single size
    authority."""
    spawn_env = os.environ.copy()
    if env:
        spawn_env.update(env)
    spawn_env["PYTHONIOENCODING"] = "utf-8"
    spawn_env.setdefault("TERM", "xterm-256color")
    return spawn_env


class PtyBackend(Protocol):
    """Cross-platform PTY backend protocol. All I/O is in raw bytes."""

    def spawn(
        self,
        cmd: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
        rows: int,
        cols: int,
    ) -> None: ...

    def read(self, size: int = 4096) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def is_alive(self) -> bool: ...

    def get_pid(self) -> int | None: ...

    def terminate(self, force: bool = False) -> None: ...

    def close(self) -> None: ...


class WindowsPtyBackend:
    """PTY backend for Windows using pywinpty (ConPTY).

    pywinpty's ``PtyProcess`` exposes a ``str``-based read/write API. We
    encode/decode at the boundary so the public surface stays bytes — every
    consumer above the backend now sees raw bytes regardless of platform.
    """

    def __init__(self) -> None:
        self._process: object | None = None  # winpty.PtyProcess at runtime

    def spawn(
        self,
        cmd: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
        rows: int,
        cols: int,
    ) -> None:
        from winpty import PtyProcess  # type: ignore[import-untyped]

        spawn_env = _terminal_env(env)

        self._process = PtyProcess.spawn(
            cmd,
            cwd=cwd,
            env=spawn_env,
            dimensions=(rows, cols),
        )
        logger.info("Windows PTY spawned pid=%s cmd=%s", self.get_pid(), cmd)

    def read(self, size: int = 4096) -> bytes:
        assert self._process is not None, "PTY not spawned"
        try:
            chunk = self._process.read(size)  # type: ignore[union-attr]
        except EOFError:
            return b""
        except Exception:
            if not self.is_alive():
                return b""
            raise
        if not chunk:
            return b""
        if isinstance(chunk, bytes):
            return chunk
        return chunk.encode("utf-8", errors="replace")

    def write(self, data: bytes) -> None:
        assert self._process is not None, "PTY not spawned"
        # pywinpty's write expects str — decode permissively. Control bytes
        # like 0x03 (Ctrl-C) and 0x1b (Esc) round-trip cleanly through latin-1
        # / utf-8 since they are below 0x80.
        text = data.decode("utf-8", errors="replace")
        self._process.write(text)  # type: ignore[union-attr]

    def resize(self, rows: int, cols: int) -> None:
        assert self._process is not None, "PTY not spawned"
        self._process.setwinsize(rows, cols)  # type: ignore[union-attr]

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.isalive()  # type: ignore[union-attr]

    def get_pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid  # type: ignore[union-attr]

    def terminate(self, force: bool = False) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate(force=force)  # type: ignore[union-attr]
        except Exception:
            logger.debug("terminate raised (process may already be dead)", exc_info=True)

    def close(self) -> None:
        if self._process is None:
            return
        try:
            self._process.close(force=True)  # type: ignore[union-attr]
        except Exception:
            logger.debug("close raised (process may already be dead)", exc_info=True)
        self._process = None


class LinuxPtyBackend:
    """PTY backend for Linux/macOS using stdlib pty module."""

    def __init__(self) -> None:
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]

    def spawn(
        self,
        cmd: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
        rows: int,
        cols: int,
    ) -> None:
        import fcntl
        import pty
        import struct
        import termios

        master_fd, slave_fd = pty.openpty()

        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        spawn_env = _terminal_env(env)

        # Runs in the forked child after the slave fd is dup'd onto stdin and
        # after setsid() (start_new_session): adopt the PTY slave as the
        # controlling terminal. Without TIOCSCTTY the tty has no foreground
        # process group, so TIOCSWINSZ resizes never deliver SIGWINCH and
        # TUIs (Claude Code, vim, htop) keep rendering at the spawn size, and
        # the line discipline cannot generate SIGINT for ^C.
        def _acquire_controlling_tty() -> None:
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self._process = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=spawn_env,
            start_new_session=True,
            preexec_fn=_acquire_controlling_tty,
            close_fds=True,
        )

        # Close slave fd in parent — child owns it now
        os.close(slave_fd)
        self._slave_fd = None
        self._master_fd = master_fd

        logger.info("Linux PTY spawned pid=%s cmd=%s", self.get_pid(), cmd)

    def read(self, size: int = 4096) -> bytes:
        assert self._master_fd is not None, "PTY not spawned"
        try:
            data = os.read(self._master_fd, size)
            if not data:
                return b""
            return data
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        import select

        assert self._master_fd is not None, "PTY not spawned"
        view = memoryview(data)
        total = 0
        while total < len(view):
            try:
                n = os.write(self._master_fd, view[total:])
            except BlockingIOError:
                # Defensive: today pty.openpty() returns a blocking fd so EAGAIN
                # never fires here. If the fd is ever switched to O_NONBLOCK,
                # block on writability via select() instead of busy-spinning —
                # the canonical POSIX retry pattern; releases the GIL.
                select.select([], [self._master_fd], [])
                continue
            if n <= 0:
                break
            total += n
        if total < len(view):
            logger.warning(
                "pty.write_short bytes_written=%d expected=%d session_master=%d",
                total, len(view), self._master_fd,
            )

    def resize(self, rows: int, cols: int) -> None:
        import fcntl
        import struct
        import termios

        assert self._master_fd is not None, "PTY not spawned"
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None

    def get_pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid

    def terminate(self, force: bool = False) -> None:
        import signal

        if self._process is None:
            return
        try:
            pgid = os.getpgid(self._process.pid)
            if force:
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            logger.debug("terminate raised (process may already be dead)", exc_info=True)

    def close(self) -> None:
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._process is not None:
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
            self._process = None


# Sentinel enqueued by FakePtyBackend.terminate()/close() to wake a blocked
# read() with EOF. A dedicated object (not None) keeps the queue element type
# unambiguous — write() only ever enqueues bytes.
_FAKE_PTY_EOF = object()


class FakePtyBackend:
    """In-process echo PTY backend for ``MOBAIDE_ENV=test`` — spawns no shell.

    Implements the :class:`PtyBackend` Protocol with deterministic echo
    semantics: bytes passed to :meth:`write` are queued straight back to
    :meth:`read`. There is **no** ``winpty``/``pty`` import, so the Windows
    ``WinptyError`` busy-loop hang the real backend can trigger is impossible.

    The load-bearing invariant: :meth:`read` **blocks** while the session is
    alive and nothing is queued — exactly like a real PTY's ``os.read``. The
    session reader loop treats an empty read as EOF (``if not data: break``)
    and relies on the blocking read to avoid busy-spinning (the real
    ``is_alive()`` is non-blocking, so a non-blocking read returning ``b""``
    while alive would spin the loop or instantly mark the session CRASHED).
    :meth:`terminate` / :meth:`close` flip ``_alive`` and enqueue an EOF
    sentinel so a blocked reader wakes promptly and teardown never hangs.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[object] = queue.Queue()
        self._leftover = b""
        self._alive = False
        self._closed = False

    def spawn(
        self,
        cmd: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
        rows: int,
        cols: int,
    ) -> None:
        # Pure echo: no banner / no unsolicited output. Readiness is signalled
        # by the session's "started" event; emitting bytes here would race into
        # the reconnect ring before a client attaches.
        self._alive = True

    def read(self, size: int = 4096) -> bytes:
        # Drain any stashed remainder first; then short-circuit to EOF when
        # dead-and-drained (never block on a terminated PTY); then block
        # waiting for echoed input.
        if self._leftover:
            chunk, self._leftover = self._leftover[:size], self._leftover[size:]
            return chunk
        if (self._closed or not self._alive) and self._queue.empty():
            return b""
        item = self._queue.get()
        if item is _FAKE_PTY_EOF:
            return b""
        assert isinstance(item, bytes)
        if len(item) > size:
            self._leftover = item[size:]
            return item[:size]
        return item

    def write(self, data: bytes) -> None:
        self._queue.put(bytes(data))

    def resize(self, rows: int, cols: int) -> None:
        return None

    def is_alive(self) -> bool:
        return self._alive

    def get_pid(self) -> int | None:
        return None

    def terminate(self, force: bool = False) -> None:
        # Flip _alive so the session's is_alive()-gated teardown skips the
        # graceful-kill grace wait, and wake any blocked read() with EOF.
        self._alive = False
        self._queue.put(_FAKE_PTY_EOF)

    def close(self) -> None:
        self._alive = False
        self._closed = True
        self._queue.put(_FAKE_PTY_EOF)


def create_pty_backend() -> PtyBackend:
    """Create the appropriate PTY backend for the current platform."""
    if sys.platform == "win32":
        return WindowsPtyBackend()  # type: ignore[return-value]
    return LinuxPtyBackend()  # type: ignore[return-value]
