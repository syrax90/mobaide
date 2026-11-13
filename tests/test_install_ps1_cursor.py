"""How ``deploy/install.ps1`` finds the Cursor CLI and writes
``MOBAIDE_CURSOR_BIN``.

``Find-CursorBinary`` runs with a PATH and profile folders that hold only
what the test puts there, so a Cursor installed on the host is neither seen
nor started. ``Set-CursorBinIfMissing`` runs the agent's real ``set-env`` on
a throwaway prefix.
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


def _find_cursor(tmp_path: Path, *, setup: str = "") -> str:
    """What ``Find-CursorBinary`` returns ("" for nothing) when PATH is
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
    body += setup + "\n\"FOUND=$(Find-CursorBinary)\"\n"
    result = _run_ps_file(tmp_path, ("Find-CursorBinary",), body)
    assert result.returncode == 0, result.stdout + result.stderr
    return _values(result.stdout)["FOUND"]


def _resolved(found: str) -> Path:
    # PowerShell on Linux may keep the ``\`` of a Windows-style relative part.
    return Path(found.replace("\\", os.sep)).resolve()


class TestFindCursorBinary:
    def test_a_cursor_agent_executable_on_path_is_found(self, tmp_path):
        fake = _fake_executable(tmp_path / "bin" / "cursor-agent.cmd")

        assert _resolved(_find_cursor(tmp_path)) == fake.resolve()

    @pytest.mark.parametrize("name", ["cursor-agent.exe", "cursor-agent.cmd"])
    def test_the_install_script_folder_is_searched_when_path_has_no_cursor(self, tmp_path, name):
        fake = _fake_executable(tmp_path / "local" / "cursor-agent" / name)

        assert _resolved(_find_cursor(tmp_path)) == fake.resolve()

    @pytest.mark.parametrize(
        "setup",
        ["function cursor-agent { 'not a binary' }", "Set-Alias -Name cursor-agent -Value Get-Date"],
        ids=["function", "alias"],
    )
    def test_a_powershell_command_named_cursor_agent_is_not_taken_for_the_cli(
        self, tmp_path, setup
    ):
        # It has no path the agent could start; the install in the folder of
        # Cursor's install script, which is not on PATH, is the real one.
        fake = _fake_executable(tmp_path / "local" / "cursor-agent" / "cursor-agent.cmd")

        assert _resolved(_find_cursor(tmp_path, setup=setup)) == fake.resolve()

    def test_the_short_name_agent_is_not_taken_for_the_cli(self, tmp_path):
        # Cursor's install script also links ``agent``; another program can
        # own a command with that name.
        _fake_executable(tmp_path / "bin" / "agent.cmd")
        _fake_executable(tmp_path / "local" / "cursor-agent" / "agent.cmd")

        assert _find_cursor(tmp_path) == ""

    def test_no_cursor_returns_nothing(self, tmp_path):
        assert _find_cursor(tmp_path) == ""


_CURSOR_BIN = r"C:\Users\Jane Doe\AppData\Local\cursor-agent\cursor-agent.cmd"


def _set_cursor_bin(tmp_path: Path, env_text: str) -> Path:
    """Run ``Set-CursorBinIfMissing`` for ``_CURSOR_BIN`` on a prefix whose
    agent ``.env`` holds ``env_text``; returns the ``.env`` path."""
    prefix = tmp_path / "Mobaide Agent"
    env_file = prefix / "mobaide_agent" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(env_text, encoding="utf-8")
    body = (
        f"$env:PYTHONPATH = '{_ps_quote(str(_REPO_ROOT / 'mobaide_agent'))}'\n"
        f"$Script:Prefix = '{_ps_quote(str(prefix))}'\n"
        "$Script:ServiceName = 'MobaideAgentTestNoSuchTask'\n"
        f"Set-CursorBinIfMissing -EnvFile '{_ps_quote(str(env_file))}' "
        f"-ReleasePython '{_ps_quote(sys.executable)}' "
        f"-CursorBin '{_ps_quote(_CURSOR_BIN)}'\n"
    )
    result = _run_ps_file(
        tmp_path,
        ("Write-Info", "Write-Warn", "Read-EnvValue", "Set-CursorBinIfMissing"),
        body,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return env_file


class TestSetCursorBinIfMissing:
    def test_writes_the_path_and_keeps_the_other_keys(self, tmp_path):
        # ``init-env`` leaves the key commented out.
        env_file = _set_cursor_bin(
            tmp_path,
            "JWT_SECRET_KEY=keep-this-secret\n"
            "RELAY_AGENT_TOKEN=keep-this-token\n"
            "# MOBAIDE_CURSOR_BIN=\n",
        )

        assert env_file.read_text(encoding="utf-8").splitlines() == [
            "JWT_SECRET_KEY=keep-this-secret",
            "RELAY_AGENT_TOKEN=keep-this-token",
            f"MOBAIDE_CURSOR_BIN={_CURSOR_BIN}",
        ]

    def test_a_path_the_operator_set_is_left_alone(self, tmp_path):
        env_text = "JWT_SECRET_KEY=keep-this-secret\nMOBAIDE_CURSOR_BIN=D:\\tools\\cursor-agent.exe\n"

        env_file = _set_cursor_bin(tmp_path, env_text)

        assert env_file.read_text(encoding="utf-8") == env_text


def test_write_agent_env_sets_the_cursor_path_with_set_env_once_the_env_file_exists() -> None:
    """``set-env`` needs an existing ``.env``, so on a fresh install it runs
    after ``init-env``; a re-install runs it too. ``init-env`` gets no Cursor
    argument: the installer is published apart from agent releases, and an
    older release fails on an argument it does not know."""
    body = _strip_powershell_comments(_function_body_text("Write-AgentEnv"))
    init_at = body.find("init-env --prefix")
    fresh_install_ends = body.find("return", init_at)
    first = body.find("Set-CursorBinIfMissing")
    last = body.rfind("Set-CursorBinIfMissing")
    assert -1 < init_at < first < fresh_install_ends < last, body
    assert "--cursor-bin" not in _strip_powershell_comments(_read_install_ps1_text())


def test_the_cursor_path_found_before_elevation_reaches_the_elevated_install() -> None:
    """The elevated window runs as another user context, where the folder of
    Cursor's install script is not the user's: the path found before the UAC
    prompt travels in ``MOBAIDE_AGENT_CURSOR_BIN_HINT``, and ``Write-AgentEnv``
    reads the hint before it searches again."""
    elevate = _strip_powershell_comments(_function_body_text("Assert-AdminOrElevate"))
    found_at = elevate.find("Find-CursorBinary")
    hint_at = elevate.find("$env:MOBAIDE_AGENT_CURSOR_BIN_HINT =")
    command_at = elevate.find("Get-ElevatedInstallCommand")
    assert -1 < found_at < hint_at < command_at, elevate

    passthrough = _strip_powershell_comments(_function_body_text("Get-ElevatedInstallCommand"))
    assert "'MOBAIDE_AGENT_CURSOR_BIN_HINT'" in passthrough

    write_env = _strip_powershell_comments(_function_body_text("Write-AgentEnv"))
    read_hint_at = write_env.find("$env:MOBAIDE_AGENT_CURSOR_BIN_HINT")
    search_at = write_env.find("Find-CursorBinary")
    assert -1 < read_hint_at < search_at, write_env
