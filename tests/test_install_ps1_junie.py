"""How ``deploy/install.ps1`` finds the Junie CLI and writes
``MOBAIDE_JUNIE_BIN``.

``Find-JunieBinary`` runs with only the PATH and profile folders the test
sets, so a host Junie is never seen. ``Set-JunieBinIfMissing`` runs the real
``set-env`` on a throwaway prefix.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from test_install_ps1 import (
    _PS,
    _REPO_ROOT,
    _function_body_text,
    _ps_quote,
    _read_install_ps1_text,
    _run_ps_file,
    _strip_powershell_comments,
    _values,
)


pytestmark = pytest.mark.skipif(
    _PS is None,
    reason="PowerShell (powershell.exe / pwsh) unavailable on this platform",
)


def _fake_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"@echo off\r\n")
    path.chmod(0o755)
    return path


def _find_junie(tmp_path: Path, *, setup: str = "") -> str:
    """What ``Find-JunieBinary`` returns ("" for nothing) when PATH is
    ``tmp_path/bin`` and the profile folders are under ``tmp_path``.
    ``setup`` runs first."""
    folders = {
        "PATH": "bin",
        "APPDATA": "roaming",
        "LOCALAPPDATA": "local",
        "USERPROFILE": "profile",
        "ProgramFiles": "programs",
    }
    body = ""
    for variable, name in folders.items():
        (tmp_path / name).mkdir(exist_ok=True)
        body += f"$env:{variable} = '{_ps_quote(str(tmp_path / name))}'\n"
    body += setup + "\n\"FOUND=$(Find-JunieBinary)\"\n"
    result = _run_ps_file(tmp_path, ("Find-JunieBinary",), body)
    assert result.returncode == 0, result.stdout + result.stderr
    return _values(result.stdout)["FOUND"]


def _resolved(found: str) -> Path:
    # PowerShell on Linux may keep the ``\`` of a Windows-style relative part.
    return Path(found.replace("\\", os.sep)).resolve()


class TestFindJunieBinary:
    def test_a_junie_launcher_on_path_is_found(self, tmp_path):
        fake = _fake_executable(tmp_path / "bin" / "junie.bat")
        # PATH wins over the folder of Junie's install script.
        _fake_executable(tmp_path / "profile" / ".local" / "bin" / "junie.bat")

        assert _resolved(_find_junie(tmp_path)) == fake.resolve()

    @pytest.mark.parametrize("name", ["junie.bat", "junie.cmd"])
    def test_the_install_script_folder_is_searched_when_path_has_no_junie(self, tmp_path, name):
        """A service account's PATH does not have the folder Junie's install
        script puts its launcher in."""
        fake = _fake_executable(tmp_path / "profile" / ".local" / "bin" / name)

        assert _resolved(_find_junie(tmp_path)) == fake.resolve()

    @pytest.mark.parametrize(
        "setup",
        ["function junie { 'not a binary' }", "Set-Alias -Name junie -Value Get-Date"],
        ids=["function", "alias"],
    )
    def test_a_powershell_command_named_junie_is_not_taken_for_the_cli(self, tmp_path, setup):
        # It has no path the agent could start; the install in the folder of
        # Junie's install script, which is not on PATH, is the real one.
        fake = _fake_executable(tmp_path / "profile" / ".local" / "bin" / "junie.bat")

        assert _resolved(_find_junie(tmp_path, setup=setup)) == fake.resolve()

    def test_no_junie_returns_nothing(self, tmp_path):
        assert _find_junie(tmp_path) == ""


_JUNIE_BIN = r"C:\Users\Jane Doe\.local\bin\junie.bat"


def _set_junie_bin(tmp_path: Path, env_text: str) -> Path:
    """Run ``Set-JunieBinIfMissing`` for ``_JUNIE_BIN`` on a prefix whose
    agent ``.env`` holds ``env_text``; returns the ``.env`` path."""
    prefix = tmp_path / "Mobaide Agent"
    env_file = prefix / "mobaide_agent" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(env_text, encoding="utf-8")
    body = (
        f"$env:PYTHONPATH = '{_ps_quote(str(_REPO_ROOT / 'mobaide_agent'))}'\n"
        f"$Script:Prefix = '{_ps_quote(str(prefix))}'\n"
        "$Script:ServiceName = 'MobaideAgentTestNoSuchTask'\n"
        f"Set-JunieBinIfMissing -EnvFile '{_ps_quote(str(env_file))}' "
        f"-ReleasePython '{_ps_quote(sys.executable)}' "
        f"-JunieBin '{_ps_quote(_JUNIE_BIN)}'\n"
    )
    result = _run_ps_file(
        tmp_path,
        ("Write-Info", "Write-Warn", "Read-EnvValue", "Set-JunieBinIfMissing"),
        body,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return env_file


class TestSetJunieBinIfMissing:
    def test_writes_the_path_and_keeps_the_other_keys(self, tmp_path):
        # ``init-env`` leaves the key commented out.
        env_file = _set_junie_bin(
            tmp_path,
            "JWT_SECRET_KEY=keep-this-secret\n"
            "RELAY_AGENT_TOKEN=keep-this-token\n"
            "# MOBAIDE_JUNIE_BIN=\n",
        )

        assert env_file.read_text(encoding="utf-8").splitlines() == [
            "JWT_SECRET_KEY=keep-this-secret",
            "RELAY_AGENT_TOKEN=keep-this-token",
            f"MOBAIDE_JUNIE_BIN={_JUNIE_BIN}",
        ]

    def test_a_path_the_operator_set_is_left_alone(self, tmp_path):
        env_text = "JWT_SECRET_KEY=keep-this-secret\nMOBAIDE_JUNIE_BIN=D:\\tools\\junie.exe\n"

        env_file = _set_junie_bin(tmp_path, env_text)

        assert env_file.read_text(encoding="utf-8") == env_text


def test_write_agent_env_sets_the_junie_path_with_set_env_once_the_env_file_exists() -> None:
    """``set-env`` needs an existing ``.env``, so on a fresh install it runs
    after ``init-env``; a re-install runs it too. ``init-env`` gets no Junie
    argument: the installer is published apart from agent releases, and an
    older release fails on an argument it does not know."""
    body = _strip_powershell_comments(_function_body_text("Write-AgentEnv"))
    init_at = body.find("init-env --prefix")
    fresh_install_ends = body.find("return", init_at)
    first = body.find("Set-JunieBinIfMissing")
    last = body.rfind("Set-JunieBinIfMissing")
    assert -1 < init_at < first < fresh_install_ends < last, body
    assert "--junie-bin" not in _strip_powershell_comments(_read_install_ps1_text())


def test_the_junie_path_found_before_elevation_reaches_the_elevated_install() -> None:
    """The elevated window runs as another user context, where the folder of
    Junie's install script is not the user's: the path found before the UAC
    prompt travels in ``MOBAIDE_AGENT_JUNIE_BIN_HINT``, and ``Write-AgentEnv``
    reads the hint before it searches again."""
    elevate = _strip_powershell_comments(_function_body_text("Assert-AdminOrElevate"))
    found_at = elevate.find("Find-JunieBinary")
    hint_at = elevate.find("$env:MOBAIDE_AGENT_JUNIE_BIN_HINT =")
    command_at = elevate.find("Get-ElevatedInstallCommand")
    assert -1 < found_at < hint_at < command_at, elevate

    passthrough = _strip_powershell_comments(_function_body_text("Get-ElevatedInstallCommand"))
    assert "'MOBAIDE_AGENT_JUNIE_BIN_HINT'" in passthrough

    write_env = _strip_powershell_comments(_function_body_text("Write-AgentEnv"))
    read_hint_at = write_env.find("$env:MOBAIDE_AGENT_JUNIE_BIN_HINT")
    search_at = write_env.find("Find-JunieBinary")
    assert -1 < read_hint_at < search_at, write_env
