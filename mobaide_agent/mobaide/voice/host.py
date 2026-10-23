"""Host resource assessment shown before the user enables voice.

``ok`` needs every threshold below; ``low_resources`` lists what falls short
(the apps warn, the user may still enable); ``unsupported`` means voice cannot
run here at all (no runtime wheel for this platform, or the disk cannot hold
the remaining install). Stdlib and ctypes only: ``psutil`` is a dev-only
dependency of the agent.
"""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

GIB = 1024**3

# A machine sold as 12 GB reports a little under 12 GiB to the OS.
OK_RAM_BYTES = int(11.5 * GIB)
OK_CPU_COUNT = 4
OK_FREE_DISK_BYTES = 2 * GIB
# Head-room kept free on top of the bytes an install still has to write.
DISK_MARGIN_BYTES = 200 * 1024 * 1024

RECOMMENDATION_OK = "ok"
RECOMMENDATION_LOW = "low_resources"
RECOMMENDATION_UNSUPPORTED = "unsupported"

_RUNTIME_REASONS = {
    "python_unsupported": (
        "Voice input needs CPython 3.12, 3.13 or 3.14 (not a free-threaded build); "
        "this agent runs Python {python}."
    ),
    "platform_unsupported": (
        "Voice input supports 64-bit Python on Windows, macOS and Linux (x86-64 or "
        "ARM64); this agent runs {bits}-bit Python on {platform}."
    ),
    "libc_unsupported": "Voice input on Linux needs glibc 2.17 or newer (musl is not supported).",
}


@dataclass(frozen=True)
class HostReason:
    code: str
    message: str


@dataclass(frozen=True)
class HostAssessment:
    ram_total_bytes: int | None
    disk_free_bytes: int | None
    cpu_count: int | None
    recommendation: str
    reasons: tuple[HostReason, ...] = field(default_factory=tuple)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def total_ram_bytes() -> int | None:
    try:
        if sys.platform == "win32":
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return None
            return int(status.ullTotalPhys)
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages <= 0 or page_size <= 0:
            return None
        return int(pages * page_size)
    except (OSError, ValueError, AttributeError):
        return None


def free_disk_bytes(path: Path) -> int | None:
    """Free bytes on the volume that holds ``path`` (or its nearest existing
    ancestor, since the voice root may not exist yet)."""
    candidate = path
    while True:
        try:
            return shutil.disk_usage(candidate).free
        except FileNotFoundError:
            parent = candidate.parent
            if parent == candidate:
                return None
            candidate = parent
        except OSError:
            return None


def runtime_reason(code: str) -> HostReason:
    template = _RUNTIME_REASONS.get(code, "Voice input is not available on this platform.")
    message = template.format(
        python=".".join(str(p) for p in sys.version_info[:3]),
        platform=f"{platform.system() or sys.platform} {platform.machine()}".strip(),
        bits=64 if sys.maxsize > 2**32 else 32,
    )
    return HostReason(code=code, message=message)


def disk_shortfall(needed_bytes: int, free: int | None) -> HostReason | None:
    """The ``disk_insufficient`` reason when ``free`` cannot hold
    ``needed_bytes`` plus the head-room margin; shared by the status
    assessment and the install's own pre-check so the two never disagree."""
    required = needed_bytes + DISK_MARGIN_BYTES
    if free is None or needed_bytes <= 0 or free >= required:
        return None
    return HostReason(
        "disk_insufficient",
        f"Voice input needs {_gb(required)} of free disk space; {_gb(free)} is free.",
    )


def assess(
    root: Path,
    *,
    remaining_bytes: int,
    runtime_reason_code: str | None,
) -> HostAssessment:
    """Assess this host for an install that still has to write
    ``remaining_bytes`` (0 once installed)."""
    ram = total_ram_bytes()
    disk = free_disk_bytes(root)
    cpus = os.cpu_count()

    blocking: list[HostReason] = []
    warnings: list[HostReason] = []
    if runtime_reason_code is not None:
        blocking.append(runtime_reason(runtime_reason_code))
    shortfall = disk_shortfall(remaining_bytes, disk)
    if shortfall is not None:
        blocking.append(shortfall)
    if ram is not None and ram < OK_RAM_BYTES:
        warnings.append(
            HostReason(
                "low_ram",
                f"This computer has {_gb(ram)} of memory; dictation uses about 700 MB "
                "while it runs, which may slow other apps.",
            )
        )
    if cpus is not None and cpus < OK_CPU_COUNT:
        warnings.append(
            HostReason(
                "low_cpu",
                f"This computer has {cpus} CPU cores; transcription may be slow.",
            )
        )
    if disk is not None and disk < OK_FREE_DISK_BYTES and not blocking:
        warnings.append(
            HostReason("low_disk", f"Only {_gb(disk)} of disk space is free.")
        )

    if blocking:
        recommendation = RECOMMENDATION_UNSUPPORTED
    elif warnings:
        recommendation = RECOMMENDATION_LOW
    else:
        recommendation = RECOMMENDATION_OK
    return HostAssessment(
        ram_total_bytes=ram,
        disk_free_bytes=disk,
        cpu_count=cpus,
        recommendation=recommendation,
        reasons=tuple(blocking + warnings),
    )


def _gb(value: int) -> str:
    return f"{value / 1_000_000_000:.1f} GB"
