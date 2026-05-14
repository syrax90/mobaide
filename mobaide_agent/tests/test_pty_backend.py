from __future__ import annotations

import logging
import time
from unittest.mock import Mock, patch

import pytest

from mobaide.core.pty_backend import (
    LinuxPtyBackend,
    WindowsPtyBackend,
    create_pty_backend,
)
from tests.conftest import IS_WINDOWS, default_shell, skip_if_not_linux


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

    def test_spawn_and_is_alive(self) -> None:
        backend = self._make_backend()
        backend.spawn(default_shell(), cwd=None, env=None, rows=24, cols=80)
        try:
            assert backend.is_alive()
            assert backend.get_pid() is not None
        finally:
            backend.terminate(force=True)
            backend.close()

    def test_write_and_read(self) -> None:
        backend = self._make_backend()
        backend.spawn(default_shell(), cwd=None, env=None, rows=24, cols=80)
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
        backend.spawn(default_shell(), cwd=None, env=None, rows=24, cols=80)
        try:
            backend.resize(40, 120)
        finally:
            backend.terminate(force=True)
            backend.close()

    def test_terminate_kills_process(self) -> None:
        backend = self._make_backend()
        backend.spawn(default_shell(), cwd=None, env=None, rows=24, cols=80)

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
        backend.spawn(default_shell(), cwd=None, env=None, rows=24, cols=80)
        backend.terminate(force=True)
        backend.close()
        backend.close()  # Second close should not raise


@skip_if_not_linux
class TestLinuxPtyBackendWrite:
    """Unit tests for ``LinuxPtyBackend.write``'s partial-write loop.

    These mock ``os.write`` at the module boundary rather than spawning a real
    PTY, because the bug under test (silently dropped bytes when ``os.write``
    returns < ``len(data)``) only manifests when the master pipe buffer fills
    up — hard to provoke deterministically against a live PTY in CI. We use
    ``Mock(side_effect=...)`` so each call returns a different value; a stub
    that always returns ``len(data)`` would silently pass the buggy single-call
    implementation too.
    """

    def _make_backend(self) -> LinuxPtyBackend:
        backend = LinuxPtyBackend()
        # Bypass spawn(): we never touch the real fd, but `write()` asserts
        # `self._master_fd is not None`, so any sentinel int works.
        backend._master_fd = 999
        return backend

    def test_linux_write_loops_on_partial_writes(self) -> None:
        backend = self._make_backend()
        data = b"x" * 5000  # > typical 4 KB PIPE_BUF
        # First os.write accepts 4096 bytes, second accepts the remainder.
        # Using side_effect=[...] forces the implementation to call os.write
        # twice — a single-call implementation would only consume 4096 bytes
        # and drop the rest.
        write_mock = Mock(side_effect=[4096, 904])

        with patch("mobaide.core.pty_backend.os.write", write_mock):
            backend.write(data)

        assert write_mock.call_count == 2, (
            "write() must loop on short returns — got "
            f"{write_mock.call_count} call(s)"
        )
        # The two calls together must cover all 5000 bytes exactly once.
        # Call 1: offered the full buffer (5000 bytes). os.write returns 4096.
        # Call 2: offered the remainder slice (904 bytes). os.write returns 904.
        # A buggy implementation that always re-offers the full buffer would
        # send the first 4096 bytes twice and silently drop the rest.
        assert len(write_mock.call_args_list[0].args[1]) == 5000, (
            "first os.write call must be offered the full buffer (5000 bytes)"
        )
        assert len(write_mock.call_args_list[1].args[1]) == 904, (
            "second os.write call must be offered ONLY the unwritten tail "
            "(904 bytes) — a fresh full buffer here means the loop is using "
            "the wrong slice and would drop bytes"
        )

    def test_linux_write_tolerates_blocking_io_error(self) -> None:
        backend = self._make_backend()
        data = b"hello world"
        # First call raises EAGAIN-style BlockingIOError, second call succeeds
        # with the full payload. The loop must NOT propagate the exception.
        write_mock = Mock(side_effect=[BlockingIOError(), len(data)])
        # The production code routes EAGAIN through `select.select(...)` to
        # wait for writability instead of busy-spinning. We patch select to a
        # no-op so it doesn't EBADF on the sentinel fd 999 used by
        # `_make_backend()`; the contract under test is "retry after EAGAIN",
        # which is observable via the os.write call count.
        select_mock = Mock(return_value=([], [999], []))

        with (
            patch("mobaide.core.pty_backend.os.write", write_mock),
            patch("select.select", select_mock),
        ):
            backend.write(data)

        assert write_mock.call_count == 2, (
            "write() must retry after BlockingIOError, not bail out"
        )
        assert select_mock.call_count == 1, (
            "write() must wait for writability via select.select between "
            "EAGAIN and the retry — busy-spin would saturate a thread on a "
            "non-blocking fd"
        )

    def test_linux_write_breaks_on_zero_return(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = self._make_backend()
        data = b"hello"
        # os.write returning 0 indicates slave-side EOF — the loop must break
        # rather than spin forever. The WARN log surfaces the silent-loss case.
        write_mock = Mock(return_value=0)

        with (
            caplog.at_level(logging.WARNING, logger="mobaide.core.pty_backend"),
            patch("mobaide.core.pty_backend.os.write", write_mock),
        ):
            backend.write(data)

        assert write_mock.call_count == 1, (
            "write() must break on n<=0; saw "
            f"{write_mock.call_count} calls (would spin forever)"
        )
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "pty.write_short" in r.getMessage()
        ]
        assert warnings, (
            "expected a pty.write_short WARN log when bytes are lost; "
            f"saw: {[r.getMessage() for r in caplog.records]}"
        )
        # Spot-check the structured fields are emitted as expected.
        msg = warnings[0].getMessage()
        assert "bytes_written=0" in msg
        assert f"expected={len(data)}" in msg
