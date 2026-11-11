"""Behavioural tests for helper functions in ``deploy/install.sh``.

Each test copies one function out of the script, runs it under bash with
stub commands on ``PATH`` and checks what it did. The function text is
embedded in the harness directly (not ``source <(sed ...)``): process
substitution sourcing defines nothing on macOS bash 3.2.

Also the shared bash lookup and function extractor for the other
``test_install_sh*.py`` modules.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest


def _find_real_bash() -> str | None:
    """On Windows, skip the WSL ``bash.exe`` stub and use Git Bash."""
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

_POSIX_STUBS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-script PATH shadowing relies on POSIX chmod",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"


@cache
def _read_install_sh_text() -> str:
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    return _DEPLOY_INSTALL_SH.read_text(encoding="utf-8")


@cache
def _sh_function_body(name: str) -> str:
    """Body of the top-level ``<name>() {`` in ``deploy/install.sh``.

    Brace counter rather than a regex, so ``{...}`` inside heredocs and
    strings does not end the body early.
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


def _sh_function(name: str) -> str:
    return f"{name}() {{\n{_sh_function_body(name)}\n}}\n"


_LOG_FUNCS = (
    "info()  { printf '[INFO]  %s\\n' \"$*\"; }\n"
    "warn()  { printf '[WARN]  %s\\n' \"$*\" >&2; }\n"
    "error() { printf '[ERROR] %s\\n' \"$*\" >&2; }\n"
    "fail()  { error \"$*\"; exit 1; }\n"
)


def _stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _python3_failing_first_call(directory: Path) -> None:
    """``python3`` stub: the first ``import`` probe fails, later ones pass."""
    count = directory / "py_count"
    _stub(
        directory,
        "python3",
        f"n=$(cat '{count}' 2>/dev/null || echo 0)\n"
        f"echo $((n+1)) > '{count}'\n"
        '[ "$n" = "0" ] && exit 1\n'
        "exit 0",
    )


def _run_function(
    name: str,
    stubs: Path,
    *,
    uid: int | None = None,
    prefix: str = "/opt/mobaide",
    version: str = "3.12",
    only_stubs_on_path: bool = False,
    epilogue: str = "",
) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    path = (
        f"PATH={shlex.quote(str(stubs))}\n"
        if only_stubs_on_path
        else f'PATH={shlex.quote(str(stubs))}:"$PATH"\n'
    )
    harness = (
        "set -euo pipefail\n"
        "PLATFORM=linux\n"
        f"MOBAIDE_AGENT_PREFIX={shlex.quote(prefix)}\n"
        f"version={version}\n"
        + _LOG_FUNCS
        + (f"id() {{ echo {uid}; }}\n" if uid is not None else "")
        + _sh_function(name)
        + path
        + f"{name}\n"
        + epilogue
    )
    return subprocess.run([_BASH, "-c", harness], capture_output=True, text=True)


def test_install_script_passes_bash_syntax_check() -> None:
    assert _BASH is not None
    proc = subprocess.run(
        [_BASH, "-n", str(_DEPLOY_INSTALL_SH)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


class TestCleanupOnExit:
    """``main()`` installs ``cleanup_on_exit`` as the EXIT trap. A ``set -e``
    abort mid-stage must remove the scratch dirs and put a moved-aside
    release back, and the trap must not trip ``set -u`` when it fires
    before any of its variables exist."""

    def _run(self, assignments: str) -> subprocess.CompletedProcess[str]:
        assert _BASH is not None
        harness = (
            "set -euo pipefail\n"
            + _sh_function("cleanup_on_exit")
            + "trap cleanup_on_exit EXIT\n"
            + "stage() {\n" + assignments + "    false\n}\n"
            + "stage\n"
        )
        return subprocess.run(
            [_BASH, "-c", harness], capture_output=True, text=True
        )

    def test_abort_restores_the_previous_release_and_removes_scratch(
        self, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        backup = releases / ".old-0.2.0-123"
        backup.mkdir(parents=True)
        (backup / ".installed").write_text("0.2.0")
        half_built = releases / "0.2.0"
        half_built.mkdir()
        (half_built / "partial").write_text("x")
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        bootstrap = tmp_path / "bootstrap"
        bootstrap.mkdir()

        proc = self._run(
            f"    tmpdir={shlex.quote(str(scratch))}\n"
            f"    BOOTSTRAP_DIR={shlex.quote(str(bootstrap))}\n"
            f"    release_dir={shlex.quote(str(half_built))}\n"
            f"    release_backup={shlex.quote(str(backup))}\n"
        )

        assert proc.returncode != 0
        assert "unbound variable" not in proc.stderr
        assert not scratch.exists()
        assert not bootstrap.exists()
        assert not backup.exists()
        assert (half_built / ".installed").read_text() == "0.2.0"
        assert not (half_built / "partial").exists()

    def test_abort_before_any_variable_is_set_is_quiet(self) -> None:
        proc = self._run("    :\n")

        assert proc.returncode == 1
        assert proc.stderr == ""


@_POSIX_STUBS
class TestEnsureEnsurepipAvailable:
    def test_is_a_noop_when_ensurepip_already_imports(self, tmp_path: Path) -> None:
        stubs = tmp_path / "bin"
        marker = tmp_path / "apt_was_called"
        _stub(stubs, "python3", "exit 0")
        _stub(stubs, "apt-get", f"echo called > '{marker}'")

        proc = _run_function("ensure_ensurepip_available", stubs, uid=0)

        assert proc.returncode == 0, proc.stderr
        assert not marker.exists()

    def test_non_root_fails_naming_the_versioned_package_and_prefix(
        self, tmp_path: Path
    ) -> None:
        stubs = tmp_path / "bin"
        _stub(stubs, "python3", "exit 1")

        proc = _run_function(
            "ensure_ensurepip_available",
            stubs,
            uid=1000,
            prefix="/home/alice/.mobaide",
            version="3.13",
        )

        assert proc.returncode != 0
        assert "without root" in proc.stderr
        assert "python3.13-venv" in proc.stderr
        assert "/home/alice/.mobaide" in proc.stderr

    def test_apt_refreshes_first_then_tries_the_versioned_package(
        self, tmp_path: Path
    ) -> None:
        stubs = tmp_path / "bin"
        log = tmp_path / "apt.log"
        _python3_failing_first_call(stubs)
        _stub(stubs, "apt-get", f"printf '%s\\n' \"$*\" >> '{log}'")

        proc = _run_function("ensure_ensurepip_available", stubs, uid=0)

        assert proc.returncode == 0, proc.stderr
        calls = log.read_text().splitlines()
        assert calls[0].startswith("update"), calls
        installs = [c for c in calls if c.startswith("install")]
        assert installs and "python3.12-venv" in installs[0], calls

    def test_a_failing_apt_update_does_not_block_the_install(
        self, tmp_path: Path
    ) -> None:
        stubs = tmp_path / "bin"
        _python3_failing_first_call(stubs)
        _stub(
            stubs,
            "apt-get",
            'if [ "$1" = "update" ]; then echo "W: mirror down" >&2; exit 100; fi\n'
            "exit 0",
        )

        proc = _run_function("ensure_ensurepip_available", stubs, uid=0)

        assert proc.returncode == 0, proc.stderr
        assert "Package manager invocation failed" not in proc.stderr

    def test_total_failure_surfaces_the_package_manager_output(
        self, tmp_path: Path
    ) -> None:
        stubs = tmp_path / "bin"
        sentinel = "E: APT_SENTINEL_dpkg_was_locked_by_another_process"
        _stub(stubs, "python3", "exit 1")
        _stub(
            stubs,
            "apt-get",
            'if [ "$1" = "update" ]; then exit 0; fi\n'
            f"echo '{sentinel}' >&2\n"
            "exit 100",
        )

        proc = _run_function("ensure_ensurepip_available", stubs, uid=0)

        assert proc.returncode != 0
        assert sentinel in proc.stderr
        assert "Package manager invocation failed" in proc.stderr

    def test_no_package_manager_on_path_fails(self, tmp_path: Path) -> None:
        stubs = tmp_path / "bin"
        _stub(stubs, "python3", "exit 1")

        proc = _run_function(
            "ensure_ensurepip_available", stubs, uid=0, only_stubs_on_path=True
        )

        assert proc.returncode != 0
        assert "No supported package manager found" in proc.stderr

    def test_fails_when_ensurepip_is_still_missing_after_the_install(
        self, tmp_path: Path
    ) -> None:
        stubs = tmp_path / "bin"
        _stub(stubs, "python3", "exit 1")
        _stub(stubs, "apt-get", "exit 0")

        proc = _run_function("ensure_ensurepip_available", stubs, uid=0)

        assert proc.returncode != 0
        assert "still fails" in proc.stderr


@_POSIX_STUBS
class TestResolveVerifierPython:
    """``BOOTSTRAP_DIR`` feeds the EXIT trap's ``rm -rf``: the fast path must
    leave it empty and never create a temp dir."""

    def _stubs(self, tmp_path: Path, python_rc: int) -> tuple[Path, Path]:
        stubs = tmp_path / "bin"
        marker = tmp_path / "mktemp_was_called"
        _stub(stubs, "python3", f"exit {python_rc}")
        _stub(stubs, "mktemp", f"echo \"$*\" > '{marker}'\nexit 1")
        return stubs, marker

    def test_fast_path_uses_python3_and_creates_nothing(self, tmp_path: Path) -> None:
        stubs, marker = self._stubs(tmp_path, python_rc=0)

        proc = _run_function(
            "resolve_verifier_python",
            stubs,
            epilogue=(
                "printf 'VERIFIER_PYTHON=<%s>\\n' \"$VERIFIER_PYTHON\"\n"
                "printf 'BOOTSTRAP_DIR=<%s>\\n' \"$BOOTSTRAP_DIR\"\n"
            ),
        )

        assert proc.returncode == 0, proc.stderr
        assert not marker.exists()
        assert "VERIFIER_PYTHON=<python3>" in proc.stdout
        assert "BOOTSTRAP_DIR=<>" in proc.stdout

    def test_slow_path_creates_a_temp_dir(self, tmp_path: Path) -> None:
        stubs, marker = self._stubs(tmp_path, python_rc=1)

        proc = _run_function("resolve_verifier_python", stubs)

        assert proc.returncode != 0
        assert marker.exists()
        assert "-d" in marker.read_text()
