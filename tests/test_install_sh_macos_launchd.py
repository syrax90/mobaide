"""Behavioural tests for ``resolve_run_as_user_macos`` in ``deploy/install.sh``.

The agent must run in the developer's GUI session, never as root. The
resolved name then reaches ``id``, ``dscl``, ``chown``, an ``eval echo ~user``
and the generated plist, so anything outside the macOS short-name charset
must be refused before the first of those runs.

``id``, ``stat`` and ``dscl`` are shell-function stubs; ``id`` records every
user name it is asked about.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from test_install_script_trap import _BASH, _sh_function


pytestmark = pytest.mark.skipif(_BASH is None, reason="bash unavailable")


def _resolve(
    tmp_path: Path, *, override: str = "", sudo_user: str = "", console: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    assert _BASH is not None
    looked_up = tmp_path / "id-lookups"
    harness = (
        "set -euo pipefail\n"
        "PLATFORM=darwin\n"
        f"MOBAIDE_RUN_AS_USER={shlex.quote(override)}\n"
        f"SUDO_USER={shlex.quote(sudo_user)}\n"
        "info() { printf '[INFO] %s\\n' \"$*\"; }\n"
        "fail() { printf '[ERROR] %s\\n' \"$*\" >&2; exit 1; }\n"
        "id() {\n"
        '  case "$1" in\n'
        '    -u) [ $# -eq 1 ] && echo 0 || echo 501 ;;\n'
        "    -gn) echo staff ;;\n"
        "    -un) echo root ;;\n"
        f"    *) printf '%s\\n' \"$1\" >> {shlex.quote(str(looked_up))} ;;\n"
        "  esac\n"
        "}\n"
        f"stat() {{ printf '%s' {shlex.quote(console)}; }}\n"
        "dscl() { printf 'NFSHomeDirectory: /Users/%s\\n' \"${3#/Users/}\"; }\n"
        + _sh_function("resolve_run_as_user_macos")
        + "resolve_run_as_user_macos\n"
        + "printf 'RUN_USER=<%s> RUN_HOME=<%s>\\n' \"$RUN_USER\" \"$RUN_HOME\"\n"
    )
    proc = subprocess.run([_BASH, "-c", harness], capture_output=True, text=True)
    lookups = looked_up.read_text().split() if looked_up.exists() else []
    return proc, lookups


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"override": "alice", "sudo_user": "bob", "console": "carol"}, "alice"),
        ({"sudo_user": "bob", "console": "carol"}, "bob"),
        ({"console": "carol"}, "carol"),
    ],
    ids=["override-wins", "sudo-user-next", "console-owner-last"],
)
def test_resolution_precedence(tmp_path, kwargs, expected) -> None:
    proc, _ = _resolve(tmp_path, **kwargs)

    assert proc.returncode == 0, proc.stderr
    assert f"RUN_USER=<{expected}> RUN_HOME=</Users/{expected}>" in proc.stdout


def test_refuses_to_run_as_root(tmp_path) -> None:
    proc, _ = _resolve(tmp_path, override="root")

    assert proc.returncode == 1
    assert "Refusing to run the agent as 'root'" in proc.stderr


@pytest.mark.parametrize(
    "kwargs",
    [{"override": "x;touch pwned"}, {"sudo_user": "$(id)"}, {"console": ""}],
    ids=["metachar-override", "subst-in-sudo-user", "empty-console-owner"],
)
def test_unsafe_names_are_refused_before_any_lookup(tmp_path, kwargs) -> None:
    proc, lookups = _resolve(tmp_path, **kwargs)

    assert proc.returncode == 1
    assert "Refusing unsafe agent username" in proc.stderr
    assert lookups == []
