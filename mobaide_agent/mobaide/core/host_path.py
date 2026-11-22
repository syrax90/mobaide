"""Find commands installed after the agent started.

A process keeps the PATH it was started with. On Windows the agent runs as a
logon task, so a CLI installed later (its installer adds a folder to the
user's or the machine's PATH in the registry) stays invisible until the agent
restarts. :func:`which_first` re-reads the saved PATH on a miss and adds the
new folders to this process's PATH, so the lookup, and every session spawned
afterwards, sees them.

On macOS and Linux the service manager gives the agent a fixed PATH and there
is no saved PATH to re-read; the per-CLI fallback folders, checked on every
lookup, cover installs made later there.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
from collections.abc import Iterable

logger = logging.getLogger(__name__)

_MACHINE_ENV_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_USER_ENV_KEY = "Environment"

_path_lock = threading.Lock()


def _saved_windows_path() -> list[str]:
    """The machine PATH, then the user PATH, as saved in the registry."""
    import winreg

    entries: list[str] = []
    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, _MACHINE_ENV_KEY),
        (winreg.HKEY_CURRENT_USER, _USER_ENV_KEY),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, kind = winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue
        if not isinstance(value, str):
            continue
        if kind == winreg.REG_EXPAND_SZ:
            value = winreg.ExpandEnvironmentStrings(value)
        entries.extend(part.strip() for part in value.split(";") if part.strip())
    return entries


def _path_key(entry: str) -> str:
    return os.path.normcase(os.path.normpath(entry))


def refresh_process_path() -> bool:
    """Add to this process's PATH the saved PATH folders it lacks.

    Appends, so a folder already on PATH keeps deciding which ``node`` or
    ``git`` runs. Returns whether anything was added; always False outside
    Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        saved = _saved_windows_path()
    except Exception:
        logger.debug("could not read the saved PATH", exc_info=True)
        return False
    with _path_lock:
        current = os.environ.get("PATH", "")
        parts = [part for part in current.split(os.pathsep) if part]
        known = {_path_key(part) for part in parts}
        added: list[str] = []
        for entry in saved:
            key = _path_key(entry)
            if key not in known:
                known.add(key)
                added.append(entry)
        if not added:
            return False
        try:
            os.environ["PATH"] = os.pathsep.join([*parts, *added])
        except (ValueError, OSError):
            # Windows caps an environment value at 32767 characters.
            logger.warning("could not extend PATH", exc_info=True)
            return False
    logger.info("Picked up new PATH folders: %s", os.pathsep.join(added))
    return True


def which_first(names: Iterable[str]) -> str | None:
    """Absolute path of the first of ``names`` on PATH, or None.

    On a miss the saved PATH is re-read once and the lookup repeated, so a
    command installed after the agent started is found without a restart.
    """
    names = tuple(names)
    for attempt in range(2):
        for name in names:
            hit = shutil.which(name)
            if hit:
                return os.path.abspath(hit)
        if attempt == 0 and not refresh_process_path():
            return None
    return None
