"""Keep this computer from idle-sleeping while the user's setting is on.

Every backend is tied to the agent process, so a crash or a kill releases it.
Linux holds locks in child processes whose stdin pipe closes when the agent
dies. Only an update restart's bridge outlives the process: for
``BRIDGE_SECONDS``, or one sleep timeout on Windows. None stops the screen
turning off or a lid close from sleeping.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

REASON = "Mobaide keeps this computer awake so your sessions stay reachable"

# An update restart leaves the computer with no lock of the agent's own until
# the next process takes one; the old process covers the gap for this long.
BRIDGE_SECONDS = 120

KeepAwakeState = Literal["off", "active", "failed", "unsupported"]


class KeepAwakeError(Exception):
    """The OS could not keep the computer awake; ``str()`` is shown to the user."""


class Backend(Protocol):
    @property
    def held(self) -> bool: ...

    @property
    def error(self) -> str | None: ...

    on_change: Callable[[], None] | None

    async def acquire(self) -> None: ...

    async def release(self) -> None: ...

    async def bridge(self) -> None:
        """Keep the computer awake past this process while the supervisor
        starts the agent again: ``BRIDGE_SECONDS`` on macOS and Linux, one
        idle-timer reset on Windows."""
        ...


_CF_STRING_ENCODING_UTF8 = 0x08000100
_IOPM_ASSERTION_LEVEL_ON = 255


class MacBackend:
    """An IOKit power assertion; macOS drops it when the process exits."""

    _TYPE = b"PreventUserIdleSystemSleep"
    _CAFFEINATE = "/usr/bin/caffeinate"

    def __init__(self) -> None:
        self.on_change: Callable[[], None] | None = None
        self._assertion: int | None = None
        self._libs: tuple[ctypes.CDLL, ctypes.CDLL] | None = None

    @property
    def held(self) -> bool:
        return self._assertion is not None

    @property
    def error(self) -> str | None:
        return None

    def _load(self) -> tuple[ctypes.CDLL, ctypes.CDLL]:
        if self._libs is None:
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            cf.CFRelease.restype = None
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
            iokit.IOPMAssertionCreateWithName.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            iokit.IOPMAssertionRelease.restype = ctypes.c_int32
            iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
            self._libs = (cf, iokit)
        return self._libs

    async def acquire(self) -> None:
        if self._assertion is not None:
            return
        try:
            cf, iokit = self._load()
        except OSError as exc:
            raise KeepAwakeError(f"Cannot load macOS power management: {exc}") from exc
        kind = cf.CFStringCreateWithCString(None, self._TYPE, _CF_STRING_ENCODING_UTF8)
        name = cf.CFStringCreateWithCString(None, REASON.encode(), _CF_STRING_ENCODING_UTF8)
        try:
            if not kind or not name:
                raise KeepAwakeError("macOS could not create the power assertion.")
            assertion = ctypes.c_uint32(0)
            result = iokit.IOPMAssertionCreateWithName(
                kind, _IOPM_ASSERTION_LEVEL_ON, name, ctypes.byref(assertion)
            )
        finally:
            for ref in (kind, name):
                if ref:
                    cf.CFRelease(ref)
        if result != 0:
            raise KeepAwakeError(
                f"macOS refused to keep the computer awake (IOReturn {result & 0xFFFFFFFF:#010x})."
            )
        self._assertion = assertion.value
        logger.info("keep_awake.macos_assertion_created id=%s", self._assertion)

    async def release(self) -> None:
        if self._assertion is None or self._libs is None:
            return
        assertion, self._assertion = self._assertion, None
        result = self._libs[1].IOPMAssertionRelease(assertion)
        if result != 0:
            logger.warning(
                "keep_awake.macos_release_failed id=%s ioreturn=%#010x", assertion, result & 0xFFFFFFFF
            )
        else:
            logger.info("keep_awake.macos_assertion_released id=%s", assertion)

    async def bridge(self) -> None:
        # ``-i`` is the same assertion. A session of its own keeps launchd
        # from stopping it with the agent's process group.
        try:
            subprocess.Popen(
                [self._CAFFEINATE, "-i", "-t", str(BRIDGE_SECONDS)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            logger.warning("keep_awake.macos_bridge_failed: %s", exc)
            return
        logger.info("keep_awake.macos_bridge seconds=%d", BRIDGE_SECONDS)


class _ReasonDetailed(ctypes.Structure):
    _fields_ = [
        ("LocalizedReasonModule", ctypes.c_void_p),
        ("LocalizedReasonId", ctypes.c_uint32),
        ("ReasonStringCount", ctypes.c_uint32),
        ("ReasonStrings", ctypes.POINTER(ctypes.c_wchar_p)),
    ]


class _ReasonUnion(ctypes.Union):
    _fields_ = [("Detailed", _ReasonDetailed), ("SimpleReasonString", ctypes.c_wchar_p)]


class _ReasonContext(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_uint32), ("Flags", ctypes.c_uint32), ("Reason", _ReasonUnion)]


_POWER_REQUEST_CONTEXT_VERSION = 0
_POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x1
_POWER_REQUEST_SYSTEM_REQUIRED = 1
_ES_SYSTEM_REQUIRED = 0x00000001
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class WindowsBackend:
    """A power request, as ``powercfg /requests`` lists it; Windows closes
    the handle, and so drops the request, when the process exits. Unlike
    ``SetThreadExecutionState`` it does not belong to the calling thread."""

    def __init__(self) -> None:
        self.on_change: Callable[[], None] | None = None
        self._handle: int | None = None
        # The request keeps pointing at the reason string while it lives.
        self._context: _ReasonContext | None = None
        self._kernel32: ctypes.CDLL | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    @property
    def error(self) -> str | None:
        return None

    def _load(self) -> ctypes.CDLL:
        kernel32 = self._kernel32
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            kernel32.PowerCreateRequest.restype = ctypes.c_void_p
            kernel32.PowerCreateRequest.argtypes = [ctypes.POINTER(_ReasonContext)]
            kernel32.PowerSetRequest.restype = ctypes.c_int
            kernel32.PowerSetRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
            kernel32.PowerClearRequest.restype = ctypes.c_int
            kernel32.PowerClearRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
            kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
            self._kernel32 = kernel32
        return kernel32

    @staticmethod
    def _last_error() -> str:
        code = ctypes.get_last_error()  # type: ignore[attr-defined]
        return f"{ctypes.FormatError(code).strip()} (error {code})"  # type: ignore[attr-defined]

    async def acquire(self) -> None:
        if self._handle is not None:
            return
        try:
            kernel32 = self._load()
        except (OSError, AttributeError) as exc:
            raise KeepAwakeError(f"Cannot load Windows power management: {exc}") from exc
        context = _ReasonContext()
        context.Version = _POWER_REQUEST_CONTEXT_VERSION
        context.Flags = _POWER_REQUEST_CONTEXT_SIMPLE_STRING
        context.Reason.SimpleReasonString = REASON
        handle = kernel32.PowerCreateRequest(ctypes.byref(context))
        if not handle or handle == _INVALID_HANDLE_VALUE:
            raise KeepAwakeError(f"Windows refused to create a power request: {self._last_error()}")
        if not kernel32.PowerSetRequest(handle, _POWER_REQUEST_SYSTEM_REQUIRED):
            message = self._last_error()
            kernel32.CloseHandle(handle)
            raise KeepAwakeError(f"Windows refused to keep the computer awake: {message}")
        self._handle, self._context = handle, context
        logger.info("keep_awake.windows_request_set")

    async def release(self) -> None:
        if self._handle is None or self._kernel32 is None:
            return
        handle, self._handle = self._handle, None
        if not self._kernel32.PowerClearRequest(handle, _POWER_REQUEST_SYSTEM_REQUIRED):
            logger.warning("keep_awake.windows_clear_failed: %s", self._last_error())
        self._kernel32.CloseHandle(handle)
        self._context = None
        logger.info("keep_awake.windows_request_cleared")

    async def bridge(self) -> None:
        # Without ES_CONTINUOUS this only resets the idle timer, so the next
        # process has a full sleep timeout to take its own request.
        if self._kernel32 is None:
            return
        ctypes.set_last_error(0)  # type: ignore[attr-defined]
        # It returns the previous state, which may itself be 0.
        if not self._kernel32.SetThreadExecutionState(_ES_SYSTEM_REQUIRED) and ctypes.get_last_error():  # type: ignore[attr-defined]
            logger.warning("keep_awake.windows_bridge_failed: %s", self._last_error())
            return
        logger.info("keep_awake.windows_bridge reset=idle_timer")


# Runs under the inhibitor once it holds the lock: the line says so, then
# ``cat`` waits for the agent's end of stdin to close.
_HOLDER = ("/bin/sh", "-c", "echo held; exec cat >/dev/null")
_READY_TIMEOUT = 5.0
_STOP_TIMEOUT = 2.0
_RETRY_SECONDS = 60.0
_MAX_ERROR_CHARS = 300
_LOGIND_USERS = Path("/run/systemd/users")


class _Hold:
    """One inhibitor lock, held for as long as its child process runs."""

    def __init__(self, label: str, on_lost: Callable[[], None]) -> None:
        self.label = label
        self.error: str | None = None
        # The inhibitor command without the holder, for the restart bridge.
        self.inhibitor: list[str] | None = None
        self._on_lost = on_lost
        self._proc: asyncio.subprocess.Process | None = None
        self._watch: asyncio.Task[None] | None = None

    @property
    def held(self) -> bool:
        return self._proc is not None

    async def take(self, inhibitor: list[str], env: dict[str, str] | None = None) -> None:
        """Raise ``KeepAwakeError`` with the inhibitor's own words when it
        cannot take the lock. Linux only, like every asyncio subprocess in
        the agent: the Windows selector loop cannot run them."""
        if self._proc is not None:
            return
        name = Path(inhibitor[0]).name
        try:
            proc = await asyncio.create_subprocess_exec(
                *inhibitor,
                *_HOLDER,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                # No controlling terminal: polkit cannot ask for a password on
                # the terminal an agent was started from.
                start_new_session=True,
            )
        except OSError as exc:
            raise KeepAwakeError(f"Cannot run {name}: {exc.strerror or exc}") from exc
        assert proc.stdout is not None and proc.stderr is not None
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), _READY_TIMEOUT)
            if line.strip() != b"held":
                # The inhibitor exited before it ran the holder: stderr says why.
                try:
                    err = await asyncio.wait_for(proc.stderr.read(), _STOP_TIMEOUT)
                except TimeoutError:
                    err = b""
                await _stop(proc)
                text = err.decode("utf-8", "replace").strip()[:_MAX_ERROR_CHARS]
                raise KeepAwakeError(text or f"{name} exited with code {proc.returncode}.")
        except TimeoutError:
            await _stop(proc)
            raise KeepAwakeError(f"{name} did not answer in time.") from None
        except BaseException:
            # Also on cancellation: nothing else knows this process, and a
            # holder left running would keep the lock until the agent exits.
            _kill_now(proc)
            raise
        self._proc = proc
        self.inhibitor = inhibitor
        self.error = None
        self._watch = asyncio.create_task(self._watch_exit(proc), name=f"keep-awake-{self.label}")

    async def _watch_exit(self, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        if self._proc is not proc:
            return
        self._proc = None
        self.error = f"The {self.label} lock ended (exit code {code})."
        logger.warning("keep_awake.lock_lost lock=%s code=%s", self.label, code)
        self._on_lost()

    async def drop(self) -> None:
        proc, self._proc = self._proc, None
        watch, self._watch = self._watch, None
        if watch is not None:
            watch.cancel()
        if proc is not None:
            await _stop(proc)


def _kill_now(proc: asyncio.subprocess.Process) -> None:
    """Synchronous, so it also runs while a cancellation unwinds."""
    if proc.stdin is not None:
        with contextlib.suppress(Exception):
            proc.stdin.close()
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def _stop(proc: asyncio.subprocess.Process) -> None:
    """Close the holder's stdin so it exits by itself; kill it if it does not."""
    if proc.stdin is not None:
        with contextlib.suppress(Exception):
            proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), _STOP_TIMEOUT)
        return
    except TimeoutError:
        pass
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), _STOP_TIMEOUT)


async def _run_briefly(argv: list[str], env: dict[str, str]) -> tuple[int | None, str, str]:
    """Run a command that ends by itself: (exit code, stdout, stderr). The
    code is ``None`` when it could not start or did not end in time.
    Linux only, as ``_Hold.take``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return None, "", f"Cannot run {Path(argv[0]).name}: {exc.strerror or exc}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), _READY_TIMEOUT)
    except TimeoutError:
        _kill_now(proc)
        return None, "", f"{Path(argv[0]).name} did not answer in time."
    except BaseException:
        _kill_now(proc)
        raise
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _file_id(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_dev, st.st_ino


# Raw bytes: any process may name itself with bytes that are not UTF-8.
def _comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_bytes().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _start_time(pid: int) -> str | None:
    """Field 22 of ``/proc/<pid>/stat``, so a reused pid does not pass for the process."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    fields = stat.rpartition(b")")[2].split()
    return fields[19].decode("ascii", "replace") if len(fields) > 19 else None


def _display_session() -> str | None:
    """This user's graphical session, as logind records it. polkit checks a
    process outside any session against this one (``sd_uid_get_display``)."""
    try:
        state = (_LOGIND_USERS / str(os.getuid())).read_bytes().decode("utf-8", "replace")
    except OSError:
        return None
    for line in state.splitlines():
        if line.startswith("DISPLAY="):
            return line.removeprefix("DISPLAY=").strip() or None
    return None


def _bus_env(bus: Path) -> dict[str, str]:
    return dict(os.environ, DBUS_SESSION_BUS_ADDRESS=f"unix:path={bus}", XDG_RUNTIME_DIR=str(bus.parent))


class LinuxBackend:
    """logind and GNOME session locks. Before a desktop login a system
    service gets only logind's ``idle`` lock, and it swaps that for
    ``sleep`` once a login allows it; GNOME honours only its own
    session-bus lock. It takes what it can and retries the missing locks
    every minute."""

    def __init__(
        self,
        systemd_inhibit: str | None,
        gnome_session_inhibit: str | None,
        runtime_dir: Path | None = None,
        *,
        busctl: str | None = None,
        systemd_run: str | None = None,
    ) -> None:
        self.on_change: Callable[[], None] | None = None
        self._systemd_inhibit = systemd_inhibit
        self._gnome_session_inhibit = gnome_session_inhibit
        self._runtime_dir = runtime_dir
        self._busctl = busctl
        self._systemd_run = systemd_run
        self._logind = _Hold("logind", self._changed)
        self._gnome = _Hold("GNOME session", self._changed)
        # The gnome-session the GNOME lock lives in: its bus socket, and the
        # session manager's pid and start time when busctl can tell.
        self._gnome_bus: tuple[int, int] | None = None
        self._gnome_session: tuple[int, str] | None = None
        self._logind_what: str | None = None
        # The graphical session a ``sleep`` lock was last tried in.
        self._sleep_tried_display: str | None = None
        self._retry: asyncio.Task[None] | None = None
        # The retry and an acquire must not both start a holder for one lock.
        self._fill_lock = asyncio.Lock()

    @property
    def held(self) -> bool:
        return self._logind.held or self._gnome.held

    @property
    def error(self) -> str | None:
        errors = [h.error for h in (self._logind, self._gnome) if h.error]
        return " ".join(errors) or None

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _session_bus(self) -> Path | None:
        runtime = self._runtime_dir or Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        bus = runtime / "bus"
        return bus if bus.exists() else None

    def _gnome_gone(self, bus: Path | None) -> bool:
        """gnome-session-inhibit waits on its child, not on the bus, so it
        outlives a log-out while the lock it took ended with the session."""
        if bus is None:
            return True
        if self._gnome_bus is not None and _file_id(bus) != self._gnome_bus:
            return True
        session = self._gnome_session
        return session is not None and _start_time(session[0]) != session[1]

    async def _session_manager(self, env: dict[str, str]) -> tuple[int, str] | None:
        """The session manager's pid and start time, or ``None`` when busctl cannot tell."""
        if self._busctl is None:
            return None
        code, out, _ = await _run_briefly(
            [
                self._busctl, "--user", "call",
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "GetConnectionUnixProcessID", "s", "org.gnome.SessionManager",
            ],
            env,
        )
        parts = out.split()  # u 1234
        if code != 0 or len(parts) != 2 or not (parts[1].isascii() and parts[1].isdecimal()):
            return None
        pid = int(parts[1])
        # In another pid namespace the bus's pid names some other process.
        # The kernel cuts comm to 15 bytes: gnome-session-b, cinnamon-sessio
        # and mate-session all own the name.
        if "sessio" not in _comm(pid):
            return None
        start = _start_time(pid)
        return (pid, start) if start is not None else None

    def _logind_command(self, what: str) -> list[str]:
        assert self._systemd_inhibit is not None
        return [
            self._systemd_inhibit,
            f"--what={what}",
            "--who=Mobaide",
            f"--why={REASON}",
            "--mode=block",
        ]

    async def _take_logind(self) -> None:
        # ``sleep`` blocks suspend without touching the screen lock, but polkit
        # grants it to a sessionless process only while its user has a
        # graphical session. Before that a system service gets ``idle``, which
        # KDE reads as "keep the screen on", not "do not suspend".
        self._sleep_tried_display = _display_session()
        for what in ("sleep", "idle"):
            try:
                await self._logind.take(self._logind_command(what))
            except KeepAwakeError as exc:
                self._logind.error = f"systemd-inhibit: {exc}"
                continue
            self._logind_what = what
            logger.info("keep_awake.logind_lock_taken what=%s", what)
            return

    async def _upgrade_logind(self) -> None:
        """Swap an ``idle`` lock for ``sleep`` once per graphical session,
        since polkit may grant it now: one process per login, none in steady
        state."""
        display = _display_session()
        if display is None or display == self._sleep_tried_display:
            return
        self._sleep_tried_display = display
        upgrade = _Hold(self._logind.label, self._changed)
        try:
            await upgrade.take(self._logind_command("sleep"))
        except KeepAwakeError as exc:
            logger.info("keep_awake.logind_sleep_refused: %s", exc)
            return
        old, self._logind, self._logind_what = self._logind, upgrade, "sleep"
        await old.drop()
        logger.info("keep_awake.logind_lock_upgraded what=sleep")
        # The old lock may have ended and been announced while this one
        # was being taken.
        self._changed()

    async def _fill(self) -> None:
        async with self._fill_lock:
            await self._fill_locked()

    async def _fill_locked(self) -> None:
        bus = self._session_bus()
        if self._gnome.held and self._gnome_gone(bus):
            logger.info("keep_awake.gnome_session_gone")
            await self._gnome.drop()
        if self._systemd_inhibit:
            if not self._logind.held:
                await self._take_logind()
            elif self._logind_what == "idle":
                await self._upgrade_logind()
        if self._gnome_session_inhibit and bus is not None and not self._gnome.held:
            env = _bus_env(bus)
            try:
                await self._gnome.take(
                    [
                        self._gnome_session_inhibit,
                        "--app-id", "mobaide",
                        "--reason", REASON,
                        "--inhibit", "suspend",
                    ],
                    env=env,
                )
            except KeepAwakeError as exc:
                self._gnome.error = f"gnome-session-inhibit: {exc}"
            else:
                self._gnome_bus = _file_id(bus)
                self._gnome_session = await self._session_manager(env)
                logger.info("keep_awake.gnome_lock_taken")
        elif not self._gnome.held:
            self._gnome.error = None

    async def _retry_missing(self) -> None:
        while True:
            await asyncio.sleep(_RETRY_SECONDS)
            was_held = (self._logind.held, self._gnome.held)
            try:
                await self._fill()
            except Exception:
                logger.exception("keep_awake.retry_failed")
            if (self._logind.held, self._gnome.held) != was_held:
                self._changed()

    async def acquire(self) -> None:
        await self._fill()
        if self._retry is None:
            self._retry = asyncio.create_task(self._retry_missing(), name="keep-awake-retry")
        if not self.held:
            raise KeepAwakeError(self.error or "No sleep lock is available on this computer.")

    async def release(self) -> None:
        retry, self._retry = self._retry, None
        if retry is not None:
            retry.cancel()
            # A retry cut short in take() kills its own holder; one that
            # already got a lock is dropped below.
            with contextlib.suppress(asyncio.CancelledError):
                await retry
        await self._logind.drop()
        await self._gnome.drop()
        self._logind.error = self._gnome.error = None
        self._logind_what = self._sleep_tried_display = None

    async def bridge(self) -> None:
        # systemd stops every process of the agent's unit before it starts
        # the next one, so the bridge runs under the user's own service
        # manager. With nobody signed in there is none, and nothing bridges.
        bus = self._session_bus()
        if self._systemd_run is None or bus is None:
            return
        env = _bus_env(bus)
        # Under the fill lock: a retry must not swap a hold while it is read.
        async with self._fill_lock:
            for hold in (self._logind, self._gnome):
                if not hold.held or hold.inhibitor is None:
                    continue
                code, _, err = await _run_briefly(
                    [
                        self._systemd_run, "--user", "--collect", "--quiet",
                        f"--setenv=DBUS_SESSION_BUS_ADDRESS={env['DBUS_SESSION_BUS_ADDRESS']}",
                        "--", *hold.inhibitor, "sleep", str(BRIDGE_SECONDS),
                    ],
                    env,
                )
                if code == 0:
                    logger.info("keep_awake.linux_bridge_started lock=%s seconds=%d", hold.label, BRIDGE_SECONDS)
                else:
                    logger.warning(
                        "keep_awake.linux_bridge_failed lock=%s: %s", hold.label, err.strip()[:_MAX_ERROR_CHARS]
                    )


WSL_MESSAGE = (
    "This agent runs inside WSL, and Windows decides when the computer sleeps. "
    "Turn this on for a Mobaide agent installed on Windows itself."
)


def _in_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        return False


def detect_backend() -> tuple[Backend | None, str | None]:
    """This OS's backend, or ``None`` and the reason it has none."""
    if sys.platform == "darwin":
        return MacBackend(), None
    if sys.platform == "win32":
        return WindowsBackend(), None
    if sys.platform.startswith("linux"):
        if _in_wsl():
            return None, WSL_MESSAGE
        systemd_inhibit = shutil.which("systemd-inhibit")
        gnome_session_inhibit = shutil.which("gnome-session-inhibit")
        if systemd_inhibit is None and gnome_session_inhibit is None:
            return None, (
                "This Linux has neither systemd-inhibit nor gnome-session-inhibit, "
                "so Mobaide cannot keep it awake."
            )
        return LinuxBackend(
            systemd_inhibit,
            gnome_session_inhibit,
            busctl=shutil.which("busctl"),
            systemd_run=shutil.which("systemd-run"),
        ), None
    return None, f"Mobaide cannot keep a {sys.platform} computer awake."


class KeepAwake:
    """The user's choice and the backend that carries it out.

    ``on_change`` fires when the backend gains or loses its lock by itself
    (Linux only); a change the caller makes through ``set`` does not fire it.
    """

    def __init__(self, backend: Backend | None, unsupported: str | None = None) -> None:
        self.on_change: Callable[[], None] | None = None
        self._backend = backend
        self._unsupported = unsupported if backend is None else None
        if backend is None and self._unsupported is None:
            self._unsupported = "Mobaide cannot keep this computer awake."
        self._enabled = False
        self._error: str | None = None
        self._bridged = False
        self._lock = asyncio.Lock()
        if backend is not None:
            backend.on_change = self._backend_changed

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _backend_changed(self) -> None:
        if self._backend is not None and self._backend.held:
            self._error = None
        if self.on_change is not None:
            self.on_change()

    async def set(self, enabled: bool) -> None:
        async with self._lock:
            backend = self._backend
            if backend is None or not enabled:
                self._enabled = enabled
                self._error = None
                if backend is not None:
                    try:
                        await backend.release()
                    except Exception:
                        logger.exception("keep_awake.release_failed")
                return
            try:
                await backend.acquire()
                self._error = None
            except KeepAwakeError as exc:
                self._error = str(exc)
                logger.warning("keep_awake.acquire_failed: %s", exc)
            except Exception as exc:
                self._error = f"Cannot keep this computer awake: {exc}"
                logger.exception("keep_awake.acquire_crashed")
            # Only now: while the lock is being taken, a snapshot still says
            # what holds, not a "failed" that has not happened.
            self._enabled = True

    def snapshot(self) -> tuple[KeepAwakeState, str | None]:
        if self._backend is None:
            return "unsupported", self._unsupported
        if not self._enabled:
            return "off", None
        if self._backend.held:
            return "active", None
        return "failed", self._error or self._backend.error or "Cannot keep this computer awake."

    async def bridge(self) -> None:
        """For an update restart: keep the computer awake past this process,
        once, while the lock is on and held."""
        async with self._lock:
            backend = self._backend
            if self._bridged or backend is None or not (self._enabled and backend.held):
                return
            self._bridged = True
            try:
                await backend.bridge()
            except Exception:
                logger.exception("keep_awake.bridge_failed")

    async def close(self, *, bridge: bool = False) -> None:
        """Release the lock. ``bridge``: the agent restarts at once, so the
        computer stays awake until the next process holds its own lock."""
        if bridge:
            await self.bridge()
        async with self._lock:
            if self._backend is not None:
                try:
                    await self._backend.release()
                except Exception:
                    logger.exception("keep_awake.release_failed")
