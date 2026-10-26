"""Find and stop the processes a Windows install is running.

Used by the CLI's Scheduled-Task restart: ``schtasks /End`` terminates only the
supervisor, and a supervisor that predates its kill-on-close Job Object leaves
the launcher and agent running. Stdlib and ctypes only.

Image paths are matched with ``ntpath`` so the selection helpers behave the
same when the tests run on a POSIX host.
"""
from __future__ import annotations

import ctypes
import ntpath
import time
from dataclasses import dataclass
from typing import Iterable

_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_ERROR_INVALID_PARAMETER = 87
_MAX_PATH = 260
_IMAGE_PATH_CHARS = 32768


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    # None when the process cannot be opened (System, protected processes).
    exe: str | None
    # FILETIME creation time; None when it cannot be read.
    created: int | None


def _install_root(prefix: str) -> str:
    return ntpath.normcase(ntpath.join(str(prefix), ""))


def _runs_from(process: ProcessInfo, root: str) -> bool:
    return process.exe is not None and ntpath.normcase(process.exe).startswith(root)


def _started_after(parent: ProcessInfo, child: ProcessInfo) -> bool:
    return (
        parent.created is not None
        and child.created is not None
        and parent.created > child.created
    )


def ancestors(processes: Iterable[ProcessInfo], pid: int) -> list[ProcessInfo]:
    """The parent chain of ``pid``, nearest first.

    Windows reuses the PID of a parent that already exited, so the walk stops
    at a "parent" that started after its child.
    """
    by_pid = {p.pid: p for p in processes}
    chain: list[ProcessInfo] = []
    seen = {pid}
    current = by_pid.get(pid)
    while current is not None:
        parent = by_pid.get(current.ppid)
        if parent is None or parent.pid in seen or _started_after(parent, current):
            break
        chain.append(parent)
        seen.add(parent.pid)
        current = parent
    return chain


def runs_inside_install(
    processes: Iterable[ProcessInfo], pid: int, prefix: str
) -> bool:
    """Whether ``pid`` was started from a process the install runs.

    True for a command typed into one of the agent's own terminal sessions.
    The install processes directly above ``pid`` are its own launch chain (the
    ``mobaide-agent.exe`` console script and the venv ``python.exe`` stub)
    and do not count.
    """
    root = _install_root(prefix)
    chain = ancestors(processes, pid)
    own = 0
    while own < len(chain) and _runs_from(chain[own], root):
        own += 1
    return any(_runs_from(p, root) for p in chain[own:])


def install_process_ids(
    processes: Iterable[ProcessInfo], prefix: str, *, keep_pid: int
) -> list[int]:
    """Every process running from ``prefix``, plus its descendants.

    ``keep_pid`` and its ancestors are left out so the calling CLI does not
    stop its own launch chain. Descendants are included because the venv
    ``python.exe`` stub lives in the prefix but the interpreter it starts does
    not.
    """
    processes = list(processes)
    root = _install_root(prefix)
    keep = {keep_pid} | {p.pid for p in ancestors(processes, keep_pid)}
    children: dict[int, list[ProcessInfo]] = {}
    for p in processes:
        children.setdefault(p.ppid, []).append(p)
    found: dict[int, ProcessInfo] = {}
    stack = [p for p in processes if _runs_from(p, root)]
    while stack:
        p = stack.pop()
        if p.pid in keep or p.pid in found:
            continue
        found[p.pid] = p
        stack.extend(c for c in children.get(p.pid, ()) if not _started_after(p, c))
    return sorted(found)


def _kernel32():
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k32.Process32NextW.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def _image_and_start(k32, pid: int) -> tuple[str | None, int | None]:
    from ctypes import wintypes

    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None, None
    try:
        exe = None
        buf = ctypes.create_unicode_buffer(_IMAGE_PATH_CHARS)
        size = wintypes.DWORD(_IMAGE_PATH_CHARS)
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            exe = buf.value
        created = None
        times = [wintypes.FILETIME() for _ in range(4)]
        if k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return exe, created
    finally:
        k32.CloseHandle(handle)


def list_processes() -> list[ProcessInfo]:
    """Snapshot of every process with its parent, image path and start time."""
    from ctypes import wintypes

    class _ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * _MAX_PATH),
        ]

    k32 = _kernel32()
    snapshot = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    pairs: list[tuple[int, int]] = []
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pairs.append((entry.th32ProcessID, entry.th32ParentProcessID))
            ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snapshot)
    return [ProcessInfo(pid, ppid, *_image_and_start(k32, pid)) for pid, ppid in pairs]


def terminate(pids: Iterable[int], *, timeout_s: float) -> list[int]:
    """Terminate ``pids`` and wait for them to exit.

    Returns the PIDs that could not be opened or did not exit in time. A PID
    that no longer exists counts as stopped.
    """
    k32 = _kernel32()
    handles: list[tuple[int, int]] = []
    remaining: list[int] = []
    for pid in pids:
        handle = k32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
        if not handle:
            if ctypes.get_last_error() != _ERROR_INVALID_PARAMETER:
                remaining.append(pid)
            continue
        k32.TerminateProcess(handle, 1)
        handles.append((pid, handle))
    deadline = time.monotonic() + timeout_s
    try:
        for pid, handle in handles:
            wait_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if k32.WaitForSingleObject(handle, wait_ms) != _WAIT_OBJECT_0:
                remaining.append(pid)
    finally:
        for _, handle in handles:
            k32.CloseHandle(handle)
    return sorted(remaining)
