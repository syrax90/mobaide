from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Protocol

logger = logging.getLogger(__name__)


class PtyBackend(Protocol):
    """Cross-platform PTY backend protocol."""

    def spawn(
        self,
        cmd: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
        rows: int,
        cols: int,
    ) -> None: ...

    def read(self, size: int = 4096) -> str: ...

    def write(self, data: str) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def is_alive(self) -> bool: ...

    def get_pid(self) -> int | None: ...

    def terminate(self, force: bool = False) -> None: ...

    def close(self) -> None: ...


class WindowsPtyBackend:
    """PTY backend for Windows using pywinpty (ConPTY)."""

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

        spawn_env = os.environ.copy()
        if env:
            spawn_env.update(env)
        spawn_env["PYTHONIOENCODING"] = "utf-8"

        self._process = PtyProcess.spawn(
            cmd,
            cwd=cwd,
            env=spawn_env,
            dimensions=(rows, cols),
        )
        logger.info("Windows PTY spawned pid=%s cmd=%s", self.get_pid(), cmd)

    def read(self, size: int = 4096) -> str:
        assert self._process is not None, "PTY not spawned"
        try:
            return self._process.read(size)  # type: ignore[union-attr]
        except EOFError:
            return ""
        except Exception:
            if not self.is_alive():
                return ""
            raise

    def write(self, data: str) -> None:
        assert self._process is not None, "PTY not spawned"
        self._process.write(data)  # type: ignore[union-attr]

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

        # Set initial terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        spawn_env = os.environ.copy()
        if env:
            spawn_env.update(env)
        spawn_env["PYTHONIOENCODING"] = "utf-8"

        self._process = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=spawn_env,
            start_new_session=True,
            close_fds=True,
        )

        # Close slave fd in parent — child owns it now
        os.close(slave_fd)
        self._slave_fd = None
        self._master_fd = master_fd

        logger.info("Linux PTY spawned pid=%s cmd=%s", self.get_pid(), cmd)

    def read(self, size: int = 4096) -> str:
        assert self._master_fd is not None, "PTY not spawned"
        try:
            data = os.read(self._master_fd, size)
            if not data:
                return ""
            return data.decode("utf-8", errors="replace")
        except OSError:
            return ""

    def write(self, data: str) -> None:
        assert self._master_fd is not None, "PTY not spawned"
        os.write(self._master_fd, data.encode("utf-8"))

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


def create_pty_backend() -> PtyBackend:
    """Create the appropriate PTY backend for the current platform."""
    if sys.platform == "win32":
        return WindowsPtyBackend()  # type: ignore[return-value]
    return LinuxPtyBackend()  # type: ignore[return-value]
