"""Four programs install the agent from the signed dependency lock: the
agent's updater, the trial install of the release tool, ``deploy/install.sh``
and ``deploy/install.ps1``. They must give pip the same options and look for
the lock under the same name. If one of them differs, a release that passes
the trial install can fail on a computer, or a computer installs with fewer
checks than the release was tested with.

The release wheel check and ``deploy/install.sh`` must also name the same
Python and glibc versions: the check proves one side of that line, and the
script refuses the other side.

The checks on the install scripts read the script text, so they run without
bash and without PowerShell.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy" / "prod"))

import release_agent as ra  # noqa: E402

import mobaide.agent.updater as updater  # noqa: E402
from test_install_script_trap import _sh_function_body  # noqa: E402

_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"
_INSTALL_PS1 = _REPO_ROOT / "deploy" / "install.ps1"


def _pip_options(command: list[str]) -> list[str]:
    """The options of a ``pip install ... -r <file>`` command, sorted."""
    return sorted(command[command.index("install") + 1 : command.index("-r")])


def _recorded_commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        commands.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return commands


def _hash_pinned_install(commands: list[list[str]]) -> list[str]:
    (command,) = [cmd for cmd in commands if "--require-hashes" in cmd]
    return command


def _script_hash_pinned_install(script: Path) -> list[str]:
    """The one hash-pinned ``pip install`` command in an install script."""
    code_lines = [
        line
        for line in script.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines).replace("\\\n", " ")
    (line,) = [
        line
        for line in code.splitlines()
        if "pip install" in line and "--require-hashes" in line
    ]
    return shlex.split(line)


@pytest.fixture
def updater_options(tmp_path, monkeypatch) -> list[str]:
    commands = _recorded_commands(monkeypatch)
    updater._finalize_wheel_install(
        tmp_path / "venv" / "bin" / "python", tmp_path / "requirements.txt",
    )
    return _pip_options(_hash_pinned_install(commands))


def test_the_trial_install_of_the_release_tool_uses_the_updater_options(
    tmp_path, monkeypatch, updater_options
) -> None:
    commands = _recorded_commands(monkeypatch)
    lock = tmp_path / ra.DEPENDENCY_LOCK_NAME
    lock.write_text("example-dependency==1.0\n", encoding="utf-8")

    ra.trial_install(
        lock, tmp_path / "mobaide_agent-1.2.3-py3-none-any.whl", "b" * 64, tmp_path,
    )

    assert _pip_options(_hash_pinned_install(commands)) == updater_options


@pytest.mark.parametrize("script", [_INSTALL_SH, _INSTALL_PS1], ids=lambda p: p.name)
def test_the_install_script_uses_the_updater_options(
    script: Path, updater_options
) -> None:
    assert _pip_options(_script_hash_pinned_install(script)) == updater_options


def test_every_installer_reads_the_lock_under_the_name_the_release_tool_writes() -> None:
    assert updater.DEPENDENCY_LOCK_NAME == ra.DEPENDENCY_LOCK_NAME
    for script in (_INSTALL_SH, _INSTALL_PS1):
        assert f'"{ra.DEPENDENCY_LOCK_NAME}"' in script.read_text(encoding="utf-8"), (
            f"{script.name} does not read {ra.DEPENDENCY_LOCK_NAME}"
        )


def test_install_sh_refuses_the_macos_versions_the_wheel_check_does_not_cover() -> None:
    """The wheel check asks for macOS wheels from ``LOCK_MACOS_MINIMUM`` on.
    ``check_macos_version`` must refuse every older macOS."""
    check = _sh_function_body("check_macos_version")
    (major,) = re.findall(r'"\$major" -lt (\d+)', check)

    assert ra.LOCK_MACOS_MINIMUM == f"{major}.0"


def test_install_sh_refuses_what_the_release_wheel_check_does_not_cover() -> None:
    """For some Python versions on glibc Linux the wheel check asks only for
    a newer glibc (``LOCK_PLATFORM_FOR_PYTHON``). ``check_python`` must refuse an
    older glibc from the same Python version on, and below the same glibc version."""
    check_python = _sh_function_body("check_python")
    (first_python_minor,) = re.findall(r'"\$minor" -ge (\d+)', check_python)
    (needed_glibc,) = re.findall(r">= \((\d+), (\d+)\)", check_python)

    covered = ra.LOCK_PLATFORM_FOR_PYTHON
    assert covered, "the wheel check has no glibc exception; remove the one in install.sh"
    assert min(int(version.split(".")[1]) for _platform, version in covered) == int(
        first_python_minor
    )
    assert {platform.split("-manylinux_")[1] for platform in covered.values()} == {
        "_".join(needed_glibc)
    }
    # install.sh refuses every glibc Linux, so the check must cover each one
    # the release targets, for every Python from the first one on.
    assert set(covered) == {
        (platform, version)
        for platform in ra.LOCK_TARGET_PLATFORMS
        if platform.endswith("-linux-gnu")
        for version in ra.LOCK_PYTHON_VERSIONS
        if int(version.split(".")[1]) >= int(first_python_minor)
    }
