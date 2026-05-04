"""Regression test for the EXIT trap in ``deploy/install.sh``.

When ``stage_release`` runs under ``set -euo pipefail`` and the embedded
Python heredoc exits non-zero (e.g., a urllib HTTPError 404), the function
unwinds before its own cleanup and the EXIT trap runs *after* the function
has popped — so any reference to a ``local`` variable in the trap body
would expand against an unset name and trip ``set -u`` with
``tmpdir: unbound variable``.

The fix drops ``local`` so ``$tmpdir`` lives at script scope; the trap
body keeps single-quoted deferred expansion, so the variable is resolved
at trap-fire time against the still-in-scope script global. The
double-quoted alternative (substitute at trap-creation) was rejected
because ``mktemp -d`` propagates ``$TMPDIR`` content into its output, so
a hostile ``TMPDIR`` containing a single quote would break out of the
inner ``'…'`` and inject commands at script exit.

Auto-skipped where ``bash`` is not on PATH (most Windows environments
without Git Bash / WSL); the Linux + macOS GitHub Actions matrix covers
the canonical environment.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _find_real_bash() -> str | None:
    """Locate a bash that actually executes our scripts.

    On Windows, ``C:\\Windows\\System32\\bash.exe`` is the WSL launcher
    stub; it preempts Git Bash via PATH from PowerShell/cmd and fails
    with ``execvpe(/bin/bash) failed`` when the user has no WSL distro
    installed. Skip the stub explicitly and pick a real Git-Bash
    install on Windows; everywhere else, defer to ``shutil.which``.
    """
    if sys.platform != "win32":
        return shutil.which("bash")

    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        os.environ.get("PROGRAMFILES", "") + r"\Git\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


_BASH = _find_real_bash()

pytestmark = pytest.mark.skipif(
    _BASH is None,
    reason="bash unavailable on this platform",
)


_FIXED_TRAP = r"""\
set -euo pipefail

stage_release() {
    # The fix: drop `local` so $tmpdir lives at script scope; the
    # single-quoted trap resolves it at trap-fire time against that
    # still-in-scope variable. No expansion-time injection sink.
    tmpdir="$(mktemp -d)"
    trap 'rm -rf -- "$tmpdir"' EXIT
    false
}

stage_release
"""

_BUGGY_TRAP = r"""\
set -euo pipefail

stage_release() {
    local tmpdir
    tmpdir="$(mktemp -d)"
    # The bug: `local` + deferred expansion — the local goes out of
    # scope when the function unwinds, then the trap fires and trips
    # `set -u` on the now-unset variable name.
    trap 'rm -rf "$tmpdir"' EXIT
    false
}

stage_release
"""


def _run(script: str) -> subprocess.CompletedProcess[str]:
    # Use the resolved absolute path so the WSL ``bash.exe`` stub on
    # Windows does not preempt Git Bash via PATH lookup. ``_BASH`` is
    # guaranteed non-None here because the module-level skipif gates it.
    assert _BASH is not None
    return subprocess.run(
        [_BASH, "-c", script], capture_output=True, text=True
    )


def test_fixed_trap_does_not_emit_unbound_variable() -> None:
    """The fix keeps stderr clean of the regression even when the
    function unwinds via ``set -e`` mid-flight."""
    proc = _run(_FIXED_TRAP)
    assert proc.returncode != 0, (
        "test setup wrong: script should exit non-zero "
        f"(stdout={proc.stdout!r}, stderr={proc.stderr!r})"
    )
    assert "unbound variable" not in proc.stderr, (
        "fix regressed — stderr contains the tmpdir-unbound-variable "
        f"error: {proc.stderr!r}"
    )


def test_buggy_trap_does_emit_unbound_variable() -> None:
    """Negative control: the original buggy pattern *does* trip
    ``set -u`` with ``tmpdir: unbound variable``. If this test ever
    starts passing without an unbound-variable error, our regression
    detector for the fixed version is no longer load-bearing."""
    proc = _run(_BUGGY_TRAP)
    assert proc.returncode != 0
    assert "tmpdir: unbound variable" in proc.stderr, (
        "negative control failed: the buggy single-quoted trap pattern "
        "did NOT emit the expected error — bash semantics may have "
        f"shifted, or the test no longer exercises the bug. stderr={proc.stderr!r}"
    )
