"""Prompt-free macOS Full Disk Access (FDA) detection.

``full_disk_access`` reports whether this process can reach FDA-gated user data.
It probes by trying to open the user's TCC database
(``~/Library/Application Support/com.apple.TCC/TCC.db``): files in the Full Disk
Access category are *silently denied* with ``PermissionError`` when the grant is
absent — macOS never raises the interactive "allow access" dialog for them — so
the probe is safe to run from a background agent without popping a modal the
remote user can't answer.

NOTE: ``mdfind`` cannot substitute here. It reads the system-wide Spotlight
index maintained by privileged daemons, so it reflects neither this process's
grant nor its denial — only the TCC.db open does.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TCC_DB = "Library/Application Support/com.apple.TCC/TCC.db"


def full_disk_access() -> bool | None:
    """Return whether this process has macOS Full Disk Access.

    ``None`` off macOS (the concept does not apply). On macOS: ``False`` when
    reading the user's TCC database is denied (no FDA), ``True`` otherwise —
    including when the file is missing, which can only happen once the caller
    can already traverse the protected directory, i.e. is not FDA-blocked.
    """
    if sys.platform != "darwin":
        return None
    try:
        db = Path.home() / _TCC_DB
    except RuntimeError:
        return None
    try:
        with db.open("rb"):
            return True
    except PermissionError:
        return False
    except OSError:
        return True
