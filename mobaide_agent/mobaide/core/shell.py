"""Login-shell resolution for terminal-mode and custom-agent-type spawns.

Single authority for "which shell do we spawn" — previously two divergent
``_DEFAULT_SHELL`` constants (``["/bin/bash", "-l"]`` in the relay client
vs ``["/bin/bash"]`` in the REST router) hardcoded bash even where the
user's login shell is zsh (macOS default).

Resolution order on POSIX: ``$SHELL`` → ``pwd.getpwuid(os.getuid()).pw_shell``
→ ``/bin/sh``. A candidate whose basename is not a known POSIX shell
(fish, nushell, nologin, …) falls back to ``/bin/sh``: exotic shells accept
``-l``/``-c`` with different rc semantics, and a stored custom command must
run under predictable POSIX quoting.
"""
from __future__ import annotations

import os
import platform

_POSIX_SHELL_ALLOWLIST = frozenset({"bash", "zsh", "sh", "dash", "ksh"})
_POSIX_FALLBACK_SHELL = "/bin/sh"


def _is_windows() -> bool:
    return platform.system() == "Windows"


def login_shell_path() -> str:
    """Resolve the user's login shell path (POSIX only)."""
    candidate = os.environ.get("SHELL", "").strip()
    if not candidate:
        try:
            import pwd

            candidate = pwd.getpwuid(os.getuid()).pw_shell or ""
        except (ImportError, KeyError, OSError):
            candidate = ""
    if not candidate:
        return _POSIX_FALLBACK_SHELL
    if os.path.basename(candidate) not in _POSIX_SHELL_ALLOWLIST:
        return _POSIX_FALLBACK_SHELL
    return candidate


def resolve_default_shell() -> list[str]:
    """Argv for a plain interactive terminal session."""
    if _is_windows():
        return ["cmd.exe"]
    return [login_shell_path(), "-l"]


def build_custom_argv(command: str) -> list[str]:
    """Argv running a stored custom-agent-type command via the login shell.

    The command is always passed as exactly ONE argv element — no string
    interpolation, no quoting layer between the registry and the shell.
    On Windows ``cmd /c`` applies its own command-line parsing rules, so
    commands containing quotes may behave differently than under POSIX.
    """
    if _is_windows():
        return ["cmd.exe", "/c", command]
    return [login_shell_path(), "-l", "-c", command]
