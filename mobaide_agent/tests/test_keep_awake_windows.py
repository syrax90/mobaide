"""``WindowsBackend`` against a fake ``kernel32`` and ``powrprof``, so it runs on
any OS and takes no real power request."""
from __future__ import annotations

import asyncio
import ctypes
import types

import pytest

from mobaide.agent.settings import keep_awake as keep_awake_module
from mobaide.agent.settings.keep_awake import STANDBY_SCREEN_NOTE, KeepAwake, WindowsBackend

_HANDLE = 0x1234
_DISPLAY = 0  # PowerRequestDisplayRequired
_SYSTEM = 1  # PowerRequestSystemRequired
_ES_SYSTEM_REQUIRED = 0x1
_ES_DISPLAY_REQUIRED = 0x2
_SYSTEM_POWER_CAPABILITIES = 4
_STATUS_BUFFER_TOO_SMALL = 0xC0000023 - (1 << 32)
_BATTERY, _MAINS, _UNKNOWN = 0, 1, 255
_UNREADABLE = None


class FakeKernel32:
    """The request types the one handle holds, the power source Windows
    reports (``None``: ``GetSystemPowerStatus`` fails) and the flags of every
    ``SetThreadExecutionState``."""

    def __init__(self, ac_line: int | None) -> None:
        self.ac_line = ac_line
        self.requests: set[int] = set()
        self.closed = False
        self.execution_states: list[int] = []
        self._read = asyncio.Event()

    def PowerCreateRequest(self, context) -> int:
        return _HANDLE

    def PowerSetRequest(self, handle: int, kind: int) -> int:
        self.requests.add(kind)
        return 1

    def PowerClearRequest(self, handle: int, kind: int) -> int:
        self.requests.discard(kind)
        return 1

    def CloseHandle(self, handle: int) -> int:
        self.closed = True
        return 1

    def SetThreadExecutionState(self, flags: int) -> int:
        self.execution_states.append(flags)
        return 0x80000000

    def GetSystemPowerStatus(self, status) -> int:
        self._read.set()
        if self.ac_line is None:
            return 0
        status._obj.ACLineStatus = self.ac_line
        return 1

    async def switch_to(self, ac_line: int | None) -> None:
        """Changes the power source and waits until the backend has read it."""
        self.ac_line = ac_line
        self._read.clear()
        await asyncio.wait_for(self._read.wait(), 5)


def _powrprof(aoac: int, batteries: int, status: int) -> types.SimpleNamespace:
    """Writes ``SYSTEM_POWER_CAPABILITIES`` as winnt.h lays it out: 76 bytes,
    ``AoAc`` at byte 20, ``SystemBatteriesPresent`` at byte 30."""

    def call_nt_power_information(level, in_buf, in_len, out_buf, out_len):
        if level != _SYSTEM_POWER_CAPABILITIES or out_len < 76:
            return _STATUS_BUFFER_TOO_SMALL
        raw = (ctypes.c_ubyte * out_len).from_buffer(out_buf._obj)
        raw[20], raw[30] = aoac, batteries
        return status

    return types.SimpleNamespace(CallNtPowerInformation=call_nt_power_information)


@pytest.fixture(autouse=True)
def _no_wait_between_power_checks(monkeypatch):
    monkeypatch.setattr(keep_awake_module, "_POWER_CHECK_SECONDS", 0)


@pytest.fixture
def windows(monkeypatch):
    """Returns a factory for a backend on a computer with the given power
    capabilities; the default is a Modern Standby laptop."""

    def make(kernel32: FakeKernel32, *, aoac: int = 1, batteries: int = 1, status: int = 0) -> WindowsBackend:
        powrprof = _powrprof(aoac, batteries, status)
        monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_: powrprof, raising=False)
        backend = WindowsBackend()
        backend._kernel32 = kernel32  # type: ignore[assignment]
        return backend

    return make


async def test_a_standby_laptop_keeps_the_screen_on_exactly_while_on_battery(windows):
    """On battery, Windows ends a Modern Standby laptop's system request 5
    minutes after its sleep timeout; only a display request keeps it awake.
    Each switch to battery also wakes a screen that already idled off once.
    A power source Windows cannot read, or reads as unknown, changes nothing."""
    kernel32 = FakeKernel32(_BATTERY)
    backend = windows(kernel32)
    woken = [_ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED]

    await backend.acquire()
    try:
        assert kernel32.requests == {_SYSTEM, _DISPLAY}
        assert kernel32.execution_states == woken

        await kernel32.switch_to(_UNREADABLE)
        assert kernel32.requests == {_SYSTEM, _DISPLAY}

        await kernel32.switch_to(_UNKNOWN)
        assert kernel32.requests == {_SYSTEM, _DISPLAY}

        await kernel32.switch_to(_MAINS)
        assert kernel32.requests == {_SYSTEM}

        await kernel32.switch_to(_BATTERY)
        assert kernel32.requests == {_SYSTEM, _DISPLAY}
        assert kernel32.execution_states == woken * 2
    finally:
        await backend.release()

    assert kernel32.requests == set()
    assert kernel32.closed
    assert not [task for task in asyncio.all_tasks() if task.get_name() == "keep-awake-power"]


@pytest.mark.parametrize(
    ("capabilities", "ac_line", "requests", "message"),
    [
        ({}, _BATTERY, {_SYSTEM, _DISPLAY}, STANDBY_SCREEN_NOTE),
        ({}, _MAINS, {_SYSTEM}, STANDBY_SCREEN_NOTE),
        ({"aoac": 0}, _BATTERY, {_SYSTEM}, None),
        ({"status": _STATUS_BUFFER_TOO_SMALL}, _BATTERY, {_SYSTEM}, None),
    ],
    ids=["standby-laptop-on-battery", "standby-laptop-on-mains", "s3-laptop", "capabilities-unreadable"],
)
async def test_only_a_standby_laptop_keeps_the_screen_on_and_says_so(windows, capabilities, ac_line, requests, message):
    """The note belongs to the computer, not to the power source: a switch
    to battery sends no event, so the note must already be there on mains."""
    kernel32 = FakeKernel32(ac_line)
    keep_awake = KeepAwake(windows(kernel32, **capabilities))

    await keep_awake.set(True)
    try:
        assert keep_awake.snapshot() == ("active", message)
        assert kernel32.requests == requests
    finally:
        await keep_awake.set(False)

    assert keep_awake.snapshot() == ("off", None)
    assert kernel32.requests == set()


@pytest.mark.parametrize(
    ("ac_line", "flags"),
    [(_BATTERY, _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED), (_MAINS, _ES_SYSTEM_REQUIRED)],
    ids=["battery", "mains"],
)
async def test_the_restart_bridge_keeps_the_screen_on_while_the_request_does(windows, monkeypatch, ac_line, flags):
    """Once the display request is gone, a screen kept on past its timeout
    turns off at once, and a standby laptop on battery sleeps with it before
    the next agent takes its own request."""
    monkeypatch.setattr(ctypes, "set_last_error", lambda code: 0, raising=False)
    kernel32 = FakeKernel32(ac_line)
    keep_awake = KeepAwake(windows(kernel32))
    await keep_awake.set(True)
    kernel32.execution_states.clear()

    await keep_awake.close(bridge=True)

    assert kernel32.execution_states == [flags]
    assert kernel32.requests == set()
