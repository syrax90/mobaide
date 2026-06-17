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
import re
import shutil
import subprocess
import sys
from functools import cache
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
    versioned package only after a successful import recheck. An
    ``apt-get update`` refresh now precedes the installs (cold-cache
    fix), so the first recorded *install* arg list — not the first call
    overall — must contain the versioned package name.
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
    # `apt-get update` runs before the installs now, so the first logged
    # line is the refresh, not an install. The ordering contract is about
    # the install attempts: the versioned package precedes the python3-venv
    # metapackage fallback. ("install" never matches the `update -qq` line.)
    logged = log.read_text().splitlines()
    install_calls = [c for c in logged if "install" in c]
    assert install_calls, (
        f"apt-get install was never invoked; logged calls: {logged!r}"
    )
    first_call = install_calls[0]
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

    This is the user's escape valve: the function now surfaces the
    package manager's captured output when an install fails, but a
    "succeeded yet ensurepip still absent" install (broken mirror,
    wrong package, hold pin) prints nothing — so the recheck is the
    backstop that catches it. Regression here would let the script
    proceed to ``python3 -m venv`` and fail with the original opaque
    error.
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


@cache
def _read_install_sh_text() -> str:
    """File text of ``deploy/install.sh`` (UTF-8, decoded once).

    Cached so the seven structural tests that call ``_sh_function_body``
    each don't re-read the file from disk. Mirrors the
    ``@cache`` decoration on ``_read_install_ps1_text`` in the PowerShell
    test module — pure file I/O so the cache is mainly a symmetry concern,
    not a perf one, but cheap symmetry is good symmetry.
    """
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    return _DEPLOY_INSTALL_SH.read_text(encoding="utf-8")


@cache
def _sh_function_body(name: str) -> str:
    """Extract the body of a top-level function in ``deploy/install.sh``.

    Pure-Python brace counter (not ``sed``) — robust to nested ``{...}``
    inside heredocs and string literals which the line-anchored ``sed``
    pattern used elsewhere in this file would mis-handle. The contract:
    a function definition starts with ``<name>() {`` on its own line and
    ends at the matching closing brace at column 0.
    """
    text = _read_install_sh_text()
    needle = f"\n{name}() {{\n"
    start = text.find(needle)
    assert start != -1, (
        f"function {name!r} not found in {_DEPLOY_INSTALL_SH}"
    )
    body_start = start + len(needle)
    depth = 1
    i = body_start
    while i < len(text) and depth > 0:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[body_start:i]
        i += 1
    raise AssertionError(f"unbalanced braces in {name}() body")


@pytest.mark.parametrize(
    "func_name",
    ["resolve_verifier_python", "cleanup_on_exit"],
)
def test_cryptography_bootstrap_function_is_defined_in_install_sh(
    func_name: str,
) -> None:
    """The two new bash functions that bootstrap ``cryptography`` for
    the pre-wheel-install verification steps must exist as top-level
    definitions in ``deploy/install.sh``.

    * ``resolve_verifier_python`` — sets ``VERIFIER_PYTHON`` to a Python
      that can ``import cryptography``. Fast path: ``python3`` already
      has it. Slow path: build an ephemeral venv in ``$(mktemp -d)``.
    * ``cleanup_on_exit`` — single installer-wide EXIT trap that
      cleans up ``$tmpdir`` (stage_release scratch) and ``$BOOTSTRAP_DIR``
      (bootstrap venv).

    If either is renamed or deleted, ``main()``'s `trap cleanup_on_exit
    EXIT` / `resolve_verifier_python` calls reference an undefined name
    and the install errors out under ``set -u``.
    """
    assert _BASH is not None
    proc = subprocess.run(
        [
            _BASH,
            "-c",
            f"grep -c '^{func_name}()' '{_DEPLOY_INSTALL_SH.as_posix()}'",
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
        f"expected exactly one {func_name}() definition in "
        f"{_DEPLOY_INSTALL_SH}, found {count}. The bootstrap-venv chain "
        f"in main() depends on both resolve_verifier_python and "
        f"cleanup_on_exit existing at top-level — a rename or deletion "
        f"breaks the fix for the cryptography-not-importable bug."
    )


def test_main_installs_centralized_exit_trap() -> None:
    """``main()`` must install ``trap cleanup_on_exit EXIT`` as its first
    real statement.

    This is the single installer-wide EXIT trap. Without it, the
    bootstrap venv created by ``resolve_verifier_python`` is leaked on
    every failed install — and on a successful install the
    ``$BOOTSTRAP_DIR`` directory stays in ``$TMPDIR`` until the OS rotates
    it. Placing the trap before ``detect_platform`` ensures the cleanup
    fires even for the earliest failure paths.
    """
    body = _sh_function_body("main")
    assert "trap cleanup_on_exit EXIT" in body, (
        f"main() must install the centralized EXIT trap "
        f"(trap cleanup_on_exit EXIT) so $tmpdir and $BOOTSTRAP_DIR are "
        f"cleaned regardless of which step fails. Not found in "
        f"body:\n{body!r}"
    )


def test_stage_release_no_longer_installs_local_trap() -> None:
    """``stage_release()`` must NOT install its own EXIT trap.

    The centralized ``cleanup_on_exit`` trap in ``main()`` covers
    ``$tmpdir``. A local ``trap '...' EXIT`` inside ``stage_release``
    would OVERRIDE the centralized one (bash has a single EXIT slot per
    shell), silently dropping the ``$BOOTSTRAP_DIR`` cleanup. A regression
    that re-introduces the local trap also re-introduces the original
    bootstrap-venv leak.

    Similarly, ``trap - EXIT`` (which the old code used to clear its
    local trap) would clear the centralized one too — equally a
    regression.
    """
    body = _sh_function_body("stage_release")
    assert "trap 'rm -rf -- \"$tmpdir\"' EXIT" not in body, (
        f"stage_release must NOT install a local 'trap rm-rf tmpdir EXIT' "
        f"— it would override the centralized cleanup_on_exit trap from "
        f"main() and silently drop the $BOOTSTRAP_DIR cleanup. The "
        f"centralized trap already handles $tmpdir via the "
        f"${{tmpdir:-}} guard. Body:\n{body!r}"
    )
    assert "trap - EXIT" not in body, (
        f"stage_release must NOT clear the EXIT trap (trap - EXIT) — "
        f"that would also clear the centralized cleanup_on_exit and "
        f"leak the bootstrap venv. Body:\n{body!r}"
    )


def test_cleanup_on_exit_handles_both_temp_dirs() -> None:
    """``cleanup_on_exit`` must remove both ``$tmpdir`` and
    ``$BOOTSTRAP_DIR`` and guard each with the ``${var:-}`` empty default.

    The empty-default guard is essential: ``set -u`` would otherwise trip
    if the trap fires before either variable was set (e.g., a failure in
    ``detect_platform`` before ``resolve_verifier_python`` and
    ``stage_release`` have run). A regression that drops the guards
    converts a clean fail-fast exit into ``unbound variable`` noise; a
    regression that drops one of the two dirs leaks that dir on failure.
    """
    body = _sh_function_body("cleanup_on_exit")
    assert re.search(r"\$\{tmpdir:-\}", body), (
        f"cleanup_on_exit must reference ${{tmpdir:-}} (empty-default "
        f"guard so set -u does not trip on a fail-fast path before "
        f"stage_release set tmpdir). Body:\n{body!r}"
    )
    assert re.search(r"\$\{BOOTSTRAP_DIR:-\}", body), (
        f"cleanup_on_exit must reference ${{BOOTSTRAP_DIR:-}} (empty-"
        f"default guard for the bootstrap venv root). Body:\n{body!r}"
    )
    assert "rm -rf" in body, (
        f"cleanup_on_exit must actually rm -rf the temp dirs; "
        f"body:\n{body!r}"
    )


@pytest.mark.parametrize(
    "func_name",
    ["_validate_trust_key_or_fail", "verify_manifest_signature"],
)
def test_cryptography_heredoc_uses_verifier_python(func_name: str) -> None:
    """The two heredocs that ``import cryptography`` must invoke
    ``"$VERIFIER_PYTHON"``, not ``python3``.

    ``_validate_trust_key_or_fail`` and ``verify_manifest_signature``
    both run BEFORE the wheel install. On a host whose ``python3`` lacks
    cryptography (macOS, Alpine, fresh Fedora, python.org installer,
    minimal containers), invoking ``python3 -`` fails with
    ``ModuleNotFoundError`` and the install aborts. The fix routes
    these two — and ONLY these two — heredocs through
    ``$VERIFIER_PYTHON``, which ``resolve_verifier_python`` populates
    with a cryptography-capable interpreter (the bootstrap venv's
    python on hosts that need it).

    Other heredocs in this script (URL parser, version extract, staging
    script) use only stdlib and must keep using ``python3``.
    """
    body = _sh_function_body(func_name)
    assert '"$VERIFIER_PYTHON" -' in body, (
        f"{func_name} must invoke \"$VERIFIER_PYTHON\" - (the "
        f"cryptography-capable interpreter from resolve_verifier_python) "
        f"for its heredoc. A regression to `python3 -` re-opens the "
        f"ModuleNotFoundError bug on every host without a pre-existing "
        f"system cryptography. Body:\n{body!r}"
    )
    assert "python3 -" not in body, (
        f"{func_name} must NOT invoke `python3 -` — that interpreter "
        f"may not have cryptography. Use \"$VERIFIER_PYTHON\" -. "
        f"Body:\n{body!r}"
    )


def test_resolve_verifier_python_pins_cryptography_43_plus() -> None:
    """``resolve_verifier_python`` must pin ``cryptography>=43.0`` in
    the bootstrap pip install.

    Matches the floor declared in ``mobaide_agent/pyproject.toml`` line
    23 so the verifier and the release wheel see the same API surface.
    A regression to an unpinned or older floor risks subtle divergence
    if the verify heredoc ever starts using a cryptography 43+ feature.
    """
    body = _sh_function_body("resolve_verifier_python")
    assert "cryptography>=43.0" in body, (
        f"resolve_verifier_python must pin 'cryptography>=43.0' "
        f"(matches mobaide_agent/pyproject.toml line 23). "
        f"Body:\n{body!r}"
    )


def test_resolve_verifier_python_uses_isolated_mode() -> None:
    """``resolve_verifier_python``'s pip install must pass ``--isolated``.

    ``--index-url`` alone is necessary but not sufficient: pip honours
    ``PIP_EXTRA_INDEX_URL`` and ``PIP_FIND_LINKS`` (and the equivalent
    ``[global] extra-index-url`` / ``find-links`` in ``pip.conf``) as
    ADDITIVE sources with no priority — an attacker who controls the
    install user's environment can publish ``cryptography==999.0.0`` on
    their mirror and pip will prefer it by version (classic
    dependency-confusion vector, CVE-2018-20225-class).

    Pip's ``--isolated`` flag is the canonical fix: it instructs pip to
    ignore environment variables AND user/system config files, leaving
    only the explicit CLI args in play. Combined with the
    ``--index-url https://pypi.org/simple`` pin, this hardens the
    bootstrap install against the full pip-config-driven hijack vector.

    A regression that drops ``--isolated`` while keeping ``--index-url``
    silently re-opens the PIP_EXTRA_INDEX_URL gap.
    """
    body = _sh_function_body("resolve_verifier_python")
    assert "--isolated" in body, (
        f"resolve_verifier_python's pip install must pass --isolated so "
        f"pip ignores PIP_EXTRA_INDEX_URL, PIP_FIND_LINKS, "
        f"PIP_TRUSTED_HOST, and ~/.pip/pip.conf / /etc/pip.conf "
        f"(--index-url alone is necessary but not sufficient — those "
        f"vars/files are additive, not overridden). Body:\n{body!r}"
    )


def test_resolve_verifier_python_pins_canonical_pypi_index() -> None:
    """``resolve_verifier_python`` must pin
    ``--index-url https://pypi.org/simple`` in the bootstrap pip install.

    Pip otherwise honours the host's ``PIP_INDEX_URL`` env var, the
    user's ``~/.pip/pip.conf``, or system-wide ``/etc/pip.conf``. On a
    host with a hostile or misconfigured pip index, the bootstrap
    install would resolve ``cryptography`` from an attacker-controlled
    mirror — a compromised wheel could then forge a passing manifest
    signature verify, defeating the very check this venv exists for.

    The release wheel install goes through the manifest's ``wheel_url``
    rather than PyPI (see ``stage_release``), so pinning canonical PyPI
    here keeps the bootstrap's trust posture aligned with the rest of
    the install. Operators behind a self-hosted PyPI mirror can
    pre-install ``cryptography>=43.0`` on the system Python to hit the
    fast path, bypassing this pin entirely.
    """
    body = _sh_function_body("resolve_verifier_python")
    assert "--index-url" in body, (
        f"resolve_verifier_python's pip install must pin --index-url "
        f"(a hostile PIP_INDEX_URL would otherwise redirect cryptography "
        f"to an attacker-controlled mirror, defeating the manifest "
        f"signature verify). Body:\n{body!r}"
    )
    assert "https://pypi.org/simple" in body, (
        f"resolve_verifier_python's --index-url must point at canonical "
        f"PyPI (https://pypi.org/simple); a different value would either "
        f"re-open the redirection gap or break for operators on the "
        f"public install URL. Body:\n{body!r}"
    )


def test_resolve_verifier_python_uses_script_scope_bootstrap_dir() -> None:
    """``resolve_verifier_python`` must set ``BOOTSTRAP_DIR=`` without
    the ``local`` keyword.

    Same script-scope idiom as ``check_python``'s ``version=`` (line 88)
    and ``stage_release``'s ``tmpdir=`` (line ~320): the centralized
    ``cleanup_on_exit`` trap resolves ``$BOOTSTRAP_DIR`` via deferred
    expansion when the script exits. If ``resolve_verifier_python``
    declares ``local BOOTSTRAP_DIR``, the variable goes out of scope when
    the function returns and the trap fires against an unset name — under
    ``set -u`` that's an ``unbound variable`` error masking the real
    install failure.
    """
    body = _sh_function_body("resolve_verifier_python")
    assert "BOOTSTRAP_DIR=" in body, (
        f"resolve_verifier_python must assign BOOTSTRAP_DIR (the "
        f"bootstrap venv root). Body:\n{body!r}"
    )
    assert not re.search(r"\blocal\s+BOOTSTRAP_DIR\b", body), (
        f"resolve_verifier_python must NOT declare BOOTSTRAP_DIR as "
        f"local — the centralized cleanup_on_exit trap resolves it via "
        f"deferred expansion from script scope. A `local` declaration "
        f"would put the variable out of scope before the trap fires and "
        f"trip `set -u` with 'BOOTSTRAP_DIR: unbound variable'. "
        f"Body:\n{body!r}"
    )


def test_resolve_verifier_python_short_circuits_when_importable() -> None:
    """``resolve_verifier_python`` must check ``import cryptography``
    BEFORE running ``python3 -m venv``.

    Fast-path optimisation: most Debian/Ubuntu hosts already have
    ``cryptography`` (system tools like cloud-init pull it in), so the
    bootstrap-venv install is wasted work for them. A regression that
    inverts the order (or drops the check) forces every install through
    the slow path — pointless network round-trip on hosts that didn't
    need it.
    """
    body = _sh_function_body("resolve_verifier_python")
    check_at = body.find("import cryptography")
    venv_at = body.find("python3 -m venv")
    assert check_at != -1, (
        f"resolve_verifier_python must probe 'import cryptography' to "
        f"short-circuit the fast path. Body:\n{body!r}"
    )
    assert venv_at != -1, (
        f"resolve_verifier_python must build a bootstrap venv on the "
        f"slow path. Body:\n{body!r}"
    )
    assert check_at < venv_at, (
        f"resolve_verifier_python must probe import cryptography BEFORE "
        f"the python3 -m venv call (fast path first). Found 'import "
        f"cryptography' at offset {check_at}, 'python3 -m venv' at "
        f"offset {venv_at}. Body:\n{body!r}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_resolve_verifier_python_fast_path_runtime_behavior(
    tmp_path: Path,
) -> None:
    """End-to-end behavioural smoke: when ``python3`` can already import
    ``cryptography``, the function must set ``VERIFIER_PYTHON=python3``,
    ``BOOTSTRAP_DIR=""``, and NEVER invoke ``mktemp -d``.

    Pure AST/grep tests (above) pin the textual ordering of the
    short-circuit but cannot prove the function actually *takes* the
    short-circuit at runtime — a subtle regression could leave the
    literals ``BOOTSTRAP_DIR=""`` / ``VERIFIER_PYTHON="python3"``
    somewhere in the body yet structurally drop the ``return 0`` that
    skips the slow path, sending every install through ``mktemp -d`` and
    ``pip install``. The textual checks all pass; the runtime contract
    silently regresses.

    Strategy: stub ``python3`` so ``python3 -c 'import cryptography'``
    succeeds (exit 0), stub ``mktemp`` as a tripwire (writes a marker
    file when invoked, then exits non-zero so a regression that DOES
    reach mktemp halts cleanly). After invoking ``resolve_verifier_python``
    via the same source-and-call harness used by the ensurepip tests,
    assert the two output variables and the absence of the tripwire
    marker — three independent witnesses that the fast path fired.

    The complementary slow-path test
    (``test_resolve_verifier_python_slow_path_invokes_mktemp``) proves
    this test's mktemp-tripwire is load-bearing: the same stub fires
    when the fast path is correctly skipped.
    """
    assert _BASH is not None

    py_stub = tmp_path / "python3"
    py_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    py_stub.chmod(0o755)

    mktemp_marker = tmp_path / "mktemp_was_called"
    mktemp_stub = tmp_path / "mktemp"
    # Tripwire stub: record the call and exit non-zero so the function
    # would `fail` cleanly if the fast path is missed (rather than
    # falling through to a real venv/pip attempt). The marker file is
    # the witness; the non-zero exit is the safety net.
    mktemp_stub.write_text(
        f"#!/usr/bin/env bash\necho \"$*\" > '{mktemp_marker}'\nexit 1\n"
    )
    mktemp_stub.chmod(0o755)

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

source <(sed -n '/^resolve_verifier_python()/,/^}}$/p' '{_DEPLOY_INSTALL_SH.as_posix()}')

resolve_verifier_python

# Emit both contract variables on dedicated lines so the test can match
# them exactly. ``BOOTSTRAP_DIR`` must be the literal empty string on
# the fast path — print it inside angle brackets so an empty value is
# distinguishable from a missing line.
printf 'VERIFIER_PYTHON=<%s>\\n' "$VERIFIER_PYTHON"
printf 'BOOTSTRAP_DIR=<%s>\\n' "$BOOTSTRAP_DIR"
"""
    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )
    assert proc.returncode == 0, (
        f"fast path should return 0 without invoking mktemp; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert not mktemp_marker.exists(), (
        f"regression: resolve_verifier_python invoked mktemp on the "
        f"fast path. The function should short-circuit after the "
        f"successful `python3 -c 'import cryptography'` probe and "
        f"return BEFORE the BOOTSTRAP_DIR=\"$(mktemp -d)\" line. "
        f"Marker content: {mktemp_marker.read_text()!r}"
    )
    assert "VERIFIER_PYTHON=<python3>" in proc.stdout, (
        f"fast path must set VERIFIER_PYTHON to the literal 'python3' "
        f"(NOT a path under BOOTSTRAP_DIR — that's the slow-path value). "
        f"A regression here surfaces as the right exit code with the "
        f"wrong interpreter wired to the trust-key + manifest heredocs. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "BOOTSTRAP_DIR=<>" in proc.stdout, (
        f"fast path must set BOOTSTRAP_DIR to the empty string. "
        f"cleanup_on_exit guards on ${{BOOTSTRAP_DIR:-}} so a non-empty "
        f"value would trigger a stray `rm -rf` on whatever path is "
        f"there — a fast-path regression that left BOOTSTRAP_DIR as "
        f"the previous value of $TMPDIR or a stale mktemp output would "
        f"delete the wrong directory on exit. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_resolve_verifier_python_slow_path_invokes_mktemp(
    tmp_path: Path,
) -> None:
    """Negative control for the fast-path test: when ``python3`` CANNOT
    import ``cryptography``, the function MUST invoke ``mktemp -d``.

    Without this complement,
    ``test_resolve_verifier_python_fast_path_runtime_behavior``'s
    mktemp-tripwire assertion is only meaningful if we know the tripwire
    fires under some condition — otherwise a regression that always
    skips mktemp (e.g., a bare ``VERIFIER_PYTHON=python3`` with no slow
    path at all) would pass the fast-path test trivially.

    Strategy: stub ``python3`` to fail every invocation (exit 1) so the
    `import cryptography` probe fails and the function enters the slow
    path. Stub ``mktemp`` as a tripwire that records the call and exits
    non-zero, so the function ``fail``s before reaching ``python3 -m
    venv`` or ``pip install``. Assert the marker file exists with the
    expected ``-d`` argument — proof the slow path is reached.
    """
    assert _BASH is not None

    py_stub = tmp_path / "python3"
    py_stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    py_stub.chmod(0o755)

    mktemp_marker = tmp_path / "mktemp_was_called"
    mktemp_stub = tmp_path / "mktemp"
    mktemp_stub.write_text(
        f"#!/usr/bin/env bash\necho \"$*\" > '{mktemp_marker}'\nexit 1\n"
    )
    mktemp_stub.chmod(0o755)

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

source <(sed -n '/^resolve_verifier_python()/,/^}}$/p' '{_DEPLOY_INSTALL_SH.as_posix()}')

resolve_verifier_python
"""
    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )
    # The function should exit non-zero — the mktemp tripwire halts the
    # slow path before pip/venv get involved. The exact exit code is
    # not pinned; what matters is the marker.
    assert proc.returncode != 0, (
        f"slow path with failing mktemp should exit non-zero; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert mktemp_marker.exists(), (
        f"negative-control failure: resolve_verifier_python did NOT "
        f"invoke mktemp on the slow path. Either the function silently "
        f"skipped both paths (the fast-path tripwire test is no longer "
        f"load-bearing), or the import-cryptography probe is not being "
        f"interpreted as failure when the python3 stub exits 1. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "-d" in mktemp_marker.read_text(), (
        f"resolve_verifier_python's mktemp invocation must pass `-d` "
        f"(directory mode); without it mktemp creates a regular file, "
        f"and the subsequent `python3 -m venv \"$BOOTSTRAP_DIR\"` would "
        f"fail with a different error. Marker contents: "
        f"{mktemp_marker.read_text()!r}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_ensure_ensurepip_apt_runs_update_before_install(
    tmp_path: Path,
) -> None:
    """The apt-get branch must run ``apt-get update`` BEFORE the first
    ``apt-get install``.

    Cold-cache fix: a fresh/minimal image (Docker ``ubuntu:24.04``,
    debootstrap, a cleaned cloud image) ships an empty
    ``/var/lib/apt/lists``, so an install before any refresh fails with
    "Unable to locate package". Ordering — refresh first, then install —
    is the contract. A regression that drops the update, or moves it
    after the install, re-opens the cold-cache failure.

    Strategy: stub ``apt-get`` to append each invocation's verb (``$1``)
    to an ordered log; ``python3`` fails the pre-flight import (to enter
    the install path) and passes the recheck. Assert ``update`` is logged
    and that it precedes the first ``install``.
    """
    assert _BASH is not None

    log = tmp_path / "apt_seq.log"

    py_count = tmp_path / "py_count"
    py_stub = tmp_path / "python3"
    py_stub.write_text(
        f"""#!/usr/bin/env bash
n=$(cat '{py_count}' 2>/dev/null || echo 0)
echo $((n+1)) > '{py_count}'
if [ "$n" = "0" ]; then exit 1; fi
exit 0
"""
    )
    py_stub.chmod(0o755)

    apt_stub = tmp_path / "apt-get"
    apt_stub.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$1" >> '{log}'
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
    verbs = log.read_text().splitlines()
    assert "update" in verbs, (
        f"apt-get update must run before the install (cold-cache fix on "
        f"fresh/minimal images with an empty /var/lib/apt/lists). It was "
        f"never invoked; logged verbs: {verbs!r}"
    )
    first_install = next(
        (i for i, v in enumerate(verbs) if v == "install"), None
    )
    assert first_install is not None, (
        f"apt-get install was never invoked; logged verbs: {verbs!r}"
    )
    assert verbs.index("update") < first_install, (
        f"apt-get update must PRECEDE the first apt-get install (refresh "
        f"the cache, then install). Got verb order: {verbs!r}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_ensure_ensurepip_apt_install_succeeds_despite_failing_update(
    tmp_path: Path,
) -> None:
    """A non-zero ``apt-get update`` must NOT block a successful install.

    This pins the exact bug that was reported: the old ``apt-get update
    && apt-get install`` chain gated the install on the refresh exit
    code, so a benign ``apt-get update`` failure (one dead mirror, an
    expired Release signature on an UNRELATED repo, a hash-sum mismatch,
    an IPv6 timeout) silently SKIPPED the install and the function failed
    with an opaque "Package manager invocation failed". The fix runs the
    refresh best-effort (``|| true``) and ignores its status.

    Strategy: stub ``apt-get`` so the ``update`` verb returns non-zero
    while ``install`` returns zero; ``python3`` fails the pre-flight
    import then passes the recheck. The function must still reach
    ``installed=1`` and exit 0. A regression that re-couples update and
    install would make this exit non-zero.
    """
    assert _BASH is not None

    py_count = tmp_path / "py_count"
    py_stub = tmp_path / "python3"
    py_stub.write_text(
        f"""#!/usr/bin/env bash
n=$(cat '{py_count}' 2>/dev/null || echo 0)
echo $((n+1)) > '{py_count}'
if [ "$n" = "0" ]; then exit 1; fi
exit 0
"""
    )
    py_stub.chmod(0o755)

    apt_stub = tmp_path / "apt-get"
    apt_stub.write_text(
        """#!/usr/bin/env bash
# Refresh fails (benign mirror/Release hiccup); install succeeds.
if [ "$1" = "update" ]; then
    echo 'W: Failed to fetch http://example/dists/x/Release  Cannot reach mirror' >&2
    exit 100
fi
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
        f"regression: a non-zero `apt-get update` blocked the install "
        f"(the original bug). The refresh is best-effort and must not "
        f"gate the install; the function should still install the venv "
        f"package and exit 0. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "Package manager invocation failed" not in proc.stderr, (
        f"the opaque 'Package manager invocation failed' must NOT appear "
        f"when the install itself succeeded — that message means the "
        f"failing update wrongly aborted the install. stderr={proc.stderr!r}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod; "
    "Git Bash on Windows does not honor Python-set executable bits",
)
def test_ensure_ensurepip_surfaces_pm_output_on_total_failure(
    tmp_path: Path,
) -> None:
    """When every package-manager attempt fails, the function must
    surface the captured apt-get output to stderr (not discard it).

    The old code piped each invocation to ``/dev/null 2>&1``, so the user
    saw "Package manager invocation failed" with zero context. The fix
    captures stdout+stderr into ``pm_out``/``apt_out`` and prints it
    before ``fail`` so the real apt error (locked dpkg, held package,
    "Unable to locate package") is actionable. A regression back to
    ``/dev/null`` makes this assertion fail.

    Strategy: stub ``apt-get`` to emit a unique error sentinel on stderr
    and exit non-zero for BOTH the versioned and metapackage installs;
    ``python3`` always fails the import probe (so the install path is
    entered and never short-circuits). Assert the sentinel reaches the
    harness stderr and that the install genuinely failed.
    """
    assert _BASH is not None

    sentinel = "E: APT_SENTINEL_dpkg_was_locked_by_another_process"

    py_stub = tmp_path / "python3"
    py_stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    py_stub.chmod(0o755)

    apt_stub = tmp_path / "apt-get"
    apt_stub.write_text(
        f"""#!/usr/bin/env bash
# update is best-effort; both installs fail and must emit the sentinel
# so the captured output (pm_out/apt_out) carries the real cause.
if [ "$1" = "update" ]; then exit 0; fi
printf '%s\\n' '{sentinel}' >&2
exit 100
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
    assert proc.returncode != 0, (
        f"total install failure should exit non-zero; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert sentinel in proc.stderr, (
        f"the real package-manager error must be surfaced to stderr on "
        f"total failure (the captured pm_out/apt_out), not discarded to "
        f"/dev/null. The sentinel apt error was not found. "
        f"stderr={proc.stderr!r}"
    )
    assert "Package manager invocation failed" in proc.stderr, (
        f"the actionable summary must still fire after surfacing the "
        f"captured output; stderr={proc.stderr!r}"
    )
