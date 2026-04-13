from __future__ import annotations

import sys
import time

import pytest

from claude_mobile.core.pty_backend import (
    LinuxPtyBackend,
    WindowsPtyBackend,
    create_pty_backend,
)

IS_WINDOWS = sys.platform == "win32"


class TestCreatePtyBackend:
    def test_returns_correct_type(self) -> None:
        backend = create_pty_backend()
        if IS_WINDOWS:
            assert isinstance(backend, WindowsPtyBackend)
        else:
            assert isinstance(backend, LinuxPtyBackend)


@pytest.mark.timeout(15)
class TestPtyBackendLifecycle:
    """Test the PTY backend on the current platform."""

    def _make_backend(self) -> WindowsPtyBackend | LinuxPtyBackend:
        if IS_WINDOWS:
            return WindowsPtyBackend()
        return LinuxPtyBackend()

    def _shell(self) -> list[str]:
        if IS_WINDOWS:
            return ["cmd.exe"]
        return ["/bin/bash"]

    def test_spawn_and_is_alive(self) -> None:
        backend = self._make_backend()
        backend.spawn(self._shell(), cwd=None, env=None, rows=24, cols=80)
        try:
            assert backend.is_alive()
            assert backend.get_pid() is not None
        finally:
            backend.terminate(force=True)
            backend.close()

    def test_write_and_read(self) -> None:
        backend = self._make_backend()
        backend.spawn(self._shell(), cwd=None, env=None, rows=24, cols=80)
        try:
            # Give the shell time to initialize
            time.sleep(0.5)

            if IS_WINDOWS:
                backend.write(b"echo PTYTEST123\r\n")
            else:
                backend.write(b"echo PTYTEST123\n")

            # Read output until we see our marker or timeout
            output = b""
            deadline = time.time() + 5.0
            while time.time() < deadline:
                data = backend.read(4096)
                if data:
                    output += data
                if b"PTYTEST123" in output:
                    break
                time.sleep(0.1)

            assert b"PTYTEST123" in output
        finally:
            backend.terminate(force=True)
            backend.close()

    def test_resize_does_not_raise(self) -> None:
        backend = self._make_backend()
        backend.spawn(self._shell(), cwd=None, env=None, rows=24, cols=80)
        try:
            backend.resize(40, 120)
        finally:
            backend.terminate(force=True)
            backend.close()

    def test_terminate_kills_process(self) -> None:
        backend = self._make_backend()
        backend.spawn(self._shell(), cwd=None, env=None, rows=24, cols=80)

        assert backend.is_alive()
        backend.terminate(force=True)

        # Wait for termination
        deadline = time.time() + 5.0
        while time.time() < deadline and backend.is_alive():
            time.sleep(0.1)

        assert not backend.is_alive()
        backend.close()

    def test_close_is_idempotent(self) -> None:
        backend = self._make_backend()
        backend.spawn(self._shell(), cwd=None, env=None, rows=24, cols=80)
        backend.terminate(force=True)
        backend.close()
        backend.close()  # Second close should not raise
