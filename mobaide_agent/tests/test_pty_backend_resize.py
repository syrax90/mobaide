"""Dead-PTY resize guards and the fake backend's resize recorder.

Before proto-v8, ``resize`` asserted the PTY was spawned — a resize racing a
session teardown crashed the caller. The guard now returns silently on an
unspawned backend and swallows ``OSError`` from a dead descriptor.
"""
from __future__ import annotations

import os

from mobaide.core.pty_backend import FakePtyBackend, LinuxPtyBackend, WindowsPtyBackend
from tests.conftest import skip_if_not_linux


class TestFakeBackendResizeRecorder:
    def test_resize_calls_are_recorded_in_order(self) -> None:
        backend = FakePtyBackend()

        backend.resize(24, 80)
        backend.resize(40, 120)

        assert backend.resize_calls == [(24, 80), (40, 120)]


@skip_if_not_linux
class TestLinuxBackendDeadPtyResize:
    def test_resize_before_spawn_returns_without_raising(self) -> None:
        backend = LinuxPtyBackend()

        assert backend.resize(40, 120) is None

    def test_resize_on_non_tty_fd_swallows_oserror(self) -> None:
        # A pipe fd is deterministic: TIOCSWINSZ raises ENOTTY every time,
        # modelling the ioctl failing against a PTY that died under us —
        # without the fd-reuse hazard a closed descriptor would carry.
        read_fd, write_fd = os.pipe()
        try:
            backend = LinuxPtyBackend()
            backend._master_fd = read_fd

            assert backend.resize(40, 120) is None
        finally:
            os.close(read_fd)
            os.close(write_fd)


class TestWindowsBackendDeadPtyResize:
    def test_resize_before_spawn_returns_without_raising(self) -> None:
        # The Windows twin has the same guard; the unspawned branch never
        # touches winpty, so it is exercisable on any host.
        backend = WindowsPtyBackend()

        assert backend.resize(40, 120) is None
