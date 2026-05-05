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


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"


def test_install_script_passes_bash_syntax_check() -> None:
    """``deploy/install.sh`` must parse cleanly under ``bash -n``.

    Cheap insurance against accidental syntax breakage. Catches the
    parse-error class of bug locally before push.
    """
    assert _BASH is not None
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    proc = subprocess.run(
        [_BASH, "-n", str(_DEPLOY_INSTALL_SH)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"bash -n failed for {_DEPLOY_INSTALL_SH}: {proc.stderr!r}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_ensure_ensurepip_is_noop_when_module_already_available(
    tmp_path: Path,
) -> None:
    """``ensure_ensurepip_available`` must short-circuit when ``import
    ensurepip`` already succeeds.

    Strategy: source the function from ``deploy/install.sh`` and stub
    ``python3`` and ``apt-get`` on PATH. If the function tries to
    install anything, the stub apt-get drops a marker file we then
    detect — so marker-absence proves the early return.
    """
    assert _BASH is not None

    py_stub = tmp_path / "python3"
    py_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    py_stub.chmod(0o755)

    marker = tmp_path / "apt_was_called"
    apt_stub = tmp_path / "apt-get"
    apt_stub.write_text(
        f"#!/usr/bin/env bash\necho called > '{marker}'\nexit 0\n"
    )
    apt_stub.chmod(0o755)

    harness = f"""
set -euo pipefail
PATH='{tmp_path}':"$PATH"
PLATFORM=linux
MOBAIDE_AGENT_PREFIX=/opt/mobaide
version=3.12
info()  {{ printf '[INFO]  %s\\n' "$*"; }}
warn()  {{ printf '[WARN]  %s\\n' "$*" >&2; }}
error() {{ printf '[ERROR] %s\\n' "$*" >&2; }}
fail()  {{ error "$*"; exit 1; }}

# Pull just the function definition from install.sh by sourcing the
# whole file via a guard that prevents main() from running.
__sourced=1
source <(sed -n '/^ensure_ensurepip_available()/,/^}}$/p' '{_DEPLOY_INSTALL_SH.as_posix()}')

ensure_ensurepip_available
"""
    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )
    assert proc.returncode == 0, (
        f"ensure_ensurepip_available failed unexpectedly: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert not marker.exists(), (
        "regression: ensure_ensurepip_available invoked apt-get even "
        "though the python3 stub reports ensurepip is already available"
    )


def test_ensure_ensurepip_is_noop_on_macos() -> None:
    """The function must early-return on non-Linux platforms.

    macOS Pythons (python.org / Homebrew) ship venv with the
    interpreter, so the auto-install logic must not run there at all.
    """
    assert _BASH is not None
    harness = f"""
set -euo pipefail
PLATFORM=darwin
MOBAIDE_AGENT_PREFIX=/usr/local/mobaide
version=3.12
info()  {{ printf '[INFO]  %s\\n' "$*"; }}
warn()  {{ printf '[WARN]  %s\\n' "$*" >&2; }}
error() {{ printf '[ERROR] %s\\n' "$*" >&2; }}
fail()  {{ error "$*"; exit 1; }}

source <(sed -n '/^ensure_ensurepip_available()/,/^}}$/p' '{_DEPLOY_INSTALL_SH.as_posix()}')

ensure_ensurepip_available
"""
    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )
    assert proc.returncode == 0, (
        f"non-Linux early-return failed: stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    assert "Installing" not in proc.stdout, (
        f"non-Linux platform should not trigger any install path; "
        f"stdout={proc.stdout!r}"
    )


def test_ensure_ensurepip_function_is_defined_in_install_script() -> None:
    """``deploy/install.sh`` must define ``ensure_ensurepip_available``.

    Pins the contract that the function exists in the canonical install
    script, so a future refactor that accidentally drops it is caught
    locally before push.
    """
    assert _BASH is not None
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    proc = subprocess.run(
        [
            _BASH,
            "-c",
            f"grep -c '^ensure_ensurepip_available()' '{_DEPLOY_INSTALL_SH.as_posix()}'",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"grep failed for {_DEPLOY_INSTALL_SH}: stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    count = int(proc.stdout.strip() or "0")
    assert count == 1, (
        f"expected exactly one ensure_ensurepip_available() definition "
        f"in {_DEPLOY_INSTALL_SH}, found {count}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_ensure_ensurepip_fails_actionably_when_not_root(
    tmp_path: Path,
) -> None:
    """Non-root invocation must ``fail`` with a message naming the
    correct ``python${{version}}-venv`` package.

    Exercises the script-scope ``$version`` contract promoted from
    ``check_python``: if that promotion ever regresses, the error
    message would expand ``python-venv`` (no version) and this test
    catches it.
    """
    assert _BASH is not None

    py_stub = tmp_path / "python3"
    py_stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    py_stub.chmod(0o755)

    harness = f"""
set -euo pipefail
PATH='{tmp_path}':"$PATH"
PLATFORM=linux
MOBAIDE_AGENT_PREFIX=/home/alice/.mobaide
version=3.13
info()  {{ printf '[INFO]  %s\\n' "$*"; }}
warn()  {{ printf '[WARN]  %s\\n' "$*" >&2; }}
error() {{ printf '[ERROR] %s\\n' "$*" >&2; }}
fail()  {{ error "$*"; exit 1; }}
id()    {{ echo 1000; }}

source <(sed -n '/^ensure_ensurepip_available()/,/^}}$/p' '{_DEPLOY_INSTALL_SH.as_posix()}')

ensure_ensurepip_available
"""
    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )
    assert proc.returncode != 0, (
        f"non-root path should fail, but returned 0; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "without root" in proc.stderr, (
        f"actionable error must explain the root requirement; "
        f"stderr={proc.stderr!r}"
    )
    assert "python3.13-venv" in proc.stderr, (
        f"actionable error must name the version-specific apt package "
        f"(regression: $version may have been demoted back to local in "
        f"check_python). stderr={proc.stderr!r}"
    )
    assert "/home/alice/.mobaide" in proc.stderr, (
        f"actionable error must echo MOBAIDE_AGENT_PREFIX so the user "
        f"can confirm where they were targeting; stderr={proc.stderr!r}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_ensure_ensurepip_apt_prefers_versioned_package_first(
    tmp_path: Path,
) -> None:
    """The apt-get branch must try ``python${{version}}-venv`` first.

    Strategy: stub ``apt-get`` to log every invocation, succeed on the
    versioned package only after a successful import recheck. The first
    recorded apt-get arg list must contain the versioned package name.
    """
    assert _BASH is not None

    log = tmp_path / "apt.log"

    # python3 stub: import ensurepip fails the first two times
    # (pre-install + post-install attempts before apt has "succeeded"),
    # then succeeds on the recheck call after apt-get returns 0. We
    # encode that by counting calls via a sidecar file.
    py_count = tmp_path / "py_count"
    py_stub = tmp_path / "python3"
    py_stub.write_text(
        f"""#!/usr/bin/env bash
n=$(cat '{py_count}' 2>/dev/null || echo 0)
echo $((n+1)) > '{py_count}'
# First call: pre-flight 'import ensurepip' check — must fail to enter
# the install path. Subsequent calls: recheck after apt — succeed.
if [ "$n" = "0" ]; then exit 1; fi
exit 0
"""
    )
    py_stub.chmod(0o755)

    apt_stub = tmp_path / "apt-get"
    apt_stub.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> '{log}'
# Succeed on the versioned-package install so we don't fall through
# to the metapackage retry; that keeps the test focused on ordering.
exit 0
"""
    )
    apt_stub.chmod(0o755)

    harness = f"""
set -euo pipefail
PATH='{tmp_path}':"$PATH"
PLATFORM=linux
MOBAIDE_AGENT_PREFIX=/opt/mobaide
version=3.12
info()  {{ printf '[INFO]  %s\\n' "$*"; }}
warn()  {{ printf '[WARN]  %s\\n' "$*" >&2; }}
error() {{ printf '[ERROR] %s\\n' "$*" >&2; }}
fail()  {{ error "$*"; exit 1; }}
id()    {{ echo 0; }}

source <(sed -n '/^ensure_ensurepip_available()/,/^}}$/p' '{_DEPLOY_INSTALL_SH.as_posix()}')

ensure_ensurepip_available
"""
    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )
    assert proc.returncode == 0, (
        f"apt path should succeed; stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    assert log.exists(), "apt-get stub was never invoked"
    first_call = log.read_text().splitlines()[0]
    assert "python3.12-venv" in first_call, (
        f"apt-get must try python${{version}}-venv first (got: "
        f"{first_call!r}). The metapackage 'python3-venv' is the "
        f"fallback, not the primary attempt."
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_ensure_ensurepip_fails_when_no_package_manager_found(
    tmp_path: Path,
) -> None:
    """No supported package manager on PATH must produce the
    ``No supported package manager found`` actionable failure.

    Sets PATH to a directory containing only the python3 stub so none
    of apt-get/dnf/yum/pacman/zypper/apk are discoverable; verifies
    the dispatch table's ``else`` arm fires with the expected text.
    """
    assert _BASH is not None

    py_stub = tmp_path / "python3"
    py_stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    py_stub.chmod(0o755)

    harness = f"""
set -euo pipefail
PLATFORM=linux
MOBAIDE_AGENT_PREFIX=/opt/mobaide
version=3.12
info()  {{ printf '[INFO]  %s\\n' "$*"; }}
warn()  {{ printf '[WARN]  %s\\n' "$*" >&2; }}
error() {{ printf '[ERROR] %s\\n' "$*" >&2; }}
fail()  {{ error "$*"; exit 1; }}
id()    {{ echo 0; }}

# Source the function body BEFORE stripping PATH — the source step
# needs sed (process-substitution helper) and bash itself, neither of
# which we're trying to hide from the function under test.
source <(sed -n '/^ensure_ensurepip_available()/,/^}}$/p' '{_DEPLOY_INSTALL_SH.as_posix()}')

# Now restrict PATH to a directory containing only the python3 stub:
# none of apt-get/dnf/yum/pacman/zypper/apk are reachable, so the
# dispatch table's else-arm is the only path the function can take.
PATH='{tmp_path}'

ensure_ensurepip_available
"""
    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )
    assert proc.returncode != 0, (
        f"no-PM path should fail; stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    assert "No supported package manager found" in proc.stderr, (
        f"actionable error must name the dispatch-table miss; "
        f"stderr={proc.stderr!r}"
    )
    for pm in ("apt-get", "dnf", "yum", "pacman", "zypper", "apk"):
        assert pm in proc.stderr, (
            f"error must enumerate the tried package managers so the "
            f"user knows what we expect; missing {pm!r} in "
            f"stderr={proc.stderr!r}"
        )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_ensure_ensurepip_fails_when_postinstall_recheck_still_fails(
    tmp_path: Path,
) -> None:
    """After a successful package-manager invocation, the function
    must re-verify ``import ensurepip`` and ``fail`` with a diagnostic
    pointer if the module is still unavailable.

    This is the user's escape valve: silencing apt's stderr is fine
    because the recheck catches the case where the install "succeeded"
    but did not actually deliver ensurepip (broken mirror, wrong
    package, hold pin). Regression here would let the script proceed
    to ``python3 -m venv`` and fail with the original opaque error.
    """
    assert _BASH is not None

    # python3 stub: ALWAYS fails import ensurepip — both pre-flight and
    # post-install. No counter required; the contract is "still fails
    # after we tried", so a constant-fail stub is the right model.
    py_stub = tmp_path / "python3"
    py_stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    py_stub.chmod(0o755)

    apt_stub = tmp_path / "apt-get"
    apt_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    apt_stub.chmod(0o755)

    harness = f"""
set -euo pipefail
PATH='{tmp_path}':"$PATH"
PLATFORM=linux
MOBAIDE_AGENT_PREFIX=/opt/mobaide
version=3.12
info()  {{ printf '[INFO]  %s\\n' "$*"; }}
warn()  {{ printf '[WARN]  %s\\n' "$*" >&2; }}
error() {{ printf '[ERROR] %s\\n' "$*" >&2; }}
fail()  {{ error "$*"; exit 1; }}
id()    {{ echo 0; }}

source <(sed -n '/^ensure_ensurepip_available()/,/^}}$/p' '{_DEPLOY_INSTALL_SH.as_posix()}')

ensure_ensurepip_available
"""
    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )
    assert proc.returncode != 0, (
        f"recheck path should fail when import still fails post-install; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "import ensurepip" in proc.stderr, (
        f"diagnostic must include the exact reproducer command; "
        f"stderr={proc.stderr!r}"
    )
    assert "uname -a" in proc.stderr or "/etc/os-release" in proc.stderr, (
        f"diagnostic must point at the env-fingerprint commands the "
        f"user should attach to an issue report; stderr={proc.stderr!r}"
    )
