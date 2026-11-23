"""Keep this computer from idle-sleeping while the user's setting is on.

Every backend is tied to the agent process, so a crash or a kill releases it.
Linux holds locks in child processes whose stdin pipe closes when the agent
dies. None stops the screen turning off or a lid close from sleeping.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

REASON = "Mobaide keeps this computer awake so your sessions stay reachable"

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


_CF_STRING_ENCODING_UTF8 = 0x08000100
_IOPM_ASSERTION_LEVEL_ON = 255


class MacBackend:
    """An IOKit power assertion; macOS drops it when the process exits."""

    _TYPE = b"PreventUserIdleSystemSleep"

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


# Runs under the inhibitor once it holds the lock: the line says so, then
# ``cat`` waits for the agent's end of stdin to close.
_HOLDER = ("/bin/sh", "-c", "echo held; exec cat >/dev/null")
_READY_TIMEOUT = 5.0
_STOP_TIMEOUT = 2.0
_RETRY_SECONDS = 60.0
_MAX_ERROR_CHARS = 300


class _Hold:
    """One inhibitor lock, held for as long as its child process runs."""

    def __init__(self, label: str, on_lost: Callable[[], None]) -> None:
        self.label = label
        self.error: str | None = None
        self._on_lost = on_lost
        self._proc: asyncio.subprocess.Process | None = None
        self._watch: asyncio.Task[None] | None = None

    @property
    def held(self) -> bool:
        return self._proc is not None

    async def take(self, argv: list[str], env: dict[str, str] | None = None) -> None:
        """Raise ``KeepAwakeError`` with the inhibitor's own words when it
        cannot take the lock."""
        if self._proc is not None:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise KeepAwakeError(f"Cannot run {Path(argv[0]).name}: {exc.strerror or exc}") from exc
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
                raise KeepAwakeError(text or f"{Path(argv[0]).name} exited with code {proc.returncode}.")
        except TimeoutError:
            await _stop(proc)
            raise KeepAwakeError(f"{Path(argv[0]).name} did not answer in time.") from None
        except BaseException:
            # Also on cancellation: nothing else knows this process, and a
            # holder left running would keep the lock until the agent exits.
            _kill_now(proc)
            raise
        self._proc = proc
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


class LinuxBackend:
    """logind and GNOME session locks. A system service may only take logind's
    ``idle`` lock, and GNOME honours only its own session-bus lock, so it
    takes what it can and retries the missing locks every minute."""

    def __init__(
        self,
        systemd_inhibit: str | None,
        gnome_session_inhibit: str | None,
        runtime_dir: Path | None = None,
    ) -> None:
        self.on_change: Callable[[], None] | None = None
        self._systemd_inhibit = systemd_inhibit
        self._gnome_session_inhibit = gnome_session_inhibit
        self._runtime_dir = runtime_dir
        self._logind = _Hold("logind", self._changed)
        self._gnome = _Hold("GNOME session", self._changed)
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

    async def _fill(self) -> None:
        async with self._fill_lock:
            await self._fill_locked()

    async def _fill_locked(self) -> None:
        if self._systemd_inhibit and not self._logind.held:
            for what in ("sleep:idle", "idle"):
                try:
                    await self._logind.take([
                        self._systemd_inhibit,
                        f"--what={what}",
                        "--who=Mobaide",
                        f"--why={REASON}",
                        "--mode=block",
                        *_HOLDER,
                    ])
                    logger.info("keep_awake.logind_lock_taken what=%s", what)
                    break
                except KeepAwakeError as exc:
                    self._logind.error = f"systemd-inhibit: {exc}"
        bus = self._session_bus() if self._gnome_session_inhibit else None
        if self._gnome_session_inhibit and bus is not None and not self._gnome.held:
            env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=f"unix:path={bus}")
            try:
                await self._gnome.take(
                    [
                        self._gnome_session_inhibit,
                        "--app-id", "mobaide",
                        "--reason", REASON,
                        "--inhibit", "suspend",
                        *_HOLDER,
                    ],
                    env=env,
                )
                logger.info("keep_awake.gnome_lock_taken")
            except KeepAwakeError as exc:
                self._gnome.error = f"gnome-session-inhibit: {exc}"
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
        return LinuxBackend(systemd_inhibit, gnome_session_inhibit), None
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

    async def close(self) -> None:
        async with self._lock:
            if self._backend is not None:
                try:
                    await self._backend.release()
                except Exception:
                    logger.exception("keep_awake.release_failed")
