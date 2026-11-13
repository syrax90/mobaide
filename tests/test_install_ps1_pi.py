"""How ``deploy/install.ps1`` finds the Pi coding agent and writes
``MOBAIDE_PI_BIN``.

``Find-PiBinary`` runs with a PATH and profile folders that hold only what the
test puts there, so a Pi installed on the host is neither seen nor started.
``Set-PiBinIfMissing`` runs the agent's real ``set-env`` on a throwaway prefix.
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


def _find_pi(tmp_path: Path, *, setup: str = "") -> str:
    """What ``Find-PiBinary`` returns ("" for nothing) when PATH is
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
    body += setup + "\n\"FOUND=$(Find-PiBinary)\"\n"
    result = _run_ps_file(tmp_path, ("Find-PiBinary",), body)
    assert result.returncode == 0, result.stdout + result.stderr
    return _values(result.stdout)["FOUND"]


def _resolved(found: str) -> Path:
    # PowerShell on Linux may keep the ``\`` of a Windows-style relative part.
    return Path(found.replace("\\", os.sep)).resolve()


class TestFindPiBinary:
    def test_a_pi_executable_on_path_is_found(self, tmp_path):
        fake = _fake_executable(tmp_path / "bin" / "pi.cmd")
        # PATH wins over the folders the installers use.
        _fake_executable(tmp_path / "roaming" / "npm" / "pi.cmd")

        assert _resolved(_find_pi(tmp_path)) == fake.resolve()

    def test_the_folder_of_pi_own_installer_is_searched_before_npm(self, tmp_path):
        """A service account's PATH does not have the folder Pi's installer
        links its launcher into."""
        fake = _fake_executable(tmp_path / "profile" / ".pi" / "agent" / "bin" / "pi.cmd")
        _fake_executable(tmp_path / "roaming" / "npm" / "pi.cmd")

        assert _resolved(_find_pi(tmp_path)) == fake.resolve()

    @pytest.mark.parametrize(
        "setup",
        ["function pi { 'not a binary' }", "Set-Alias -Name pi -Value Get-Date"],
        ids=["function", "alias"],
    )
    def test_a_powershell_command_named_pi_is_not_taken_for_the_cli(self, tmp_path, setup):
        # It has no path the agent could start; the npm-global install that
        # is not on PATH is the real one.
        fake = _fake_executable(tmp_path / "roaming" / "npm" / "pi.cmd")

        assert _resolved(_find_pi(tmp_path, setup=setup)) == fake.resolve()

    def test_no_pi_returns_nothing(self, tmp_path):
        assert _find_pi(tmp_path) == ""


_PI_BIN = r"C:\Users\Jane Doe\.pi\agent\bin\pi.cmd"


def _set_pi_bin(tmp_path: Path, env_text: str) -> Path:
    """Run ``Set-PiBinIfMissing`` for ``_PI_BIN`` on a prefix whose agent
    ``.env`` holds ``env_text``; returns the ``.env`` path."""
    prefix = tmp_path / "Mobaide Agent"
    env_file = prefix / "mobaide_agent" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(env_text, encoding="utf-8")
    body = (
        f"$env:PYTHONPATH = '{_ps_quote(str(_REPO_ROOT / 'mobaide_agent'))}'\n"
        f"$Script:Prefix = '{_ps_quote(str(prefix))}'\n"
        "$Script:ServiceName = 'MobaideAgentTestNoSuchTask'\n"
        f"Set-PiBinIfMissing -EnvFile '{_ps_quote(str(env_file))}' "
        f"-ReleasePython '{_ps_quote(sys.executable)}' "
        f"-PiBin '{_ps_quote(_PI_BIN)}'\n"
    )
    result = _run_ps_file(
        tmp_path,
        ("Write-Info", "Write-Warn", "Read-EnvValue", "Set-PiBinIfMissing"),
        body,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return env_file


class TestSetPiBinIfMissing:
    def test_writes_the_path_and_keeps_the_other_keys(self, tmp_path):
        # ``init-env`` leaves the key commented out.
        env_file = _set_pi_bin(
            tmp_path,
            "JWT_SECRET_KEY=keep-this-secret\n"
            "RELAY_AGENT_TOKEN=keep-this-token\n"
            "# MOBAIDE_PI_BIN=\n",
        )

        assert env_file.read_text(encoding="utf-8").splitlines() == [
            "JWT_SECRET_KEY=keep-this-secret",
            "RELAY_AGENT_TOKEN=keep-this-token",
            f"MOBAIDE_PI_BIN={_PI_BIN}",
        ]

    def test_a_path_the_operator_set_is_left_alone(self, tmp_path):
        env_text = "JWT_SECRET_KEY=keep-this-secret\nMOBAIDE_PI_BIN=D:\\tools\\pi.exe\n"

        env_file = _set_pi_bin(tmp_path, env_text)

        assert env_file.read_text(encoding="utf-8") == env_text


def test_write_agent_env_sets_the_pi_path_with_set_env_once_the_env_file_exists() -> None:
    """``set-env`` needs an existing ``.env``, so on a fresh install it runs
    after ``init-env``; a re-install runs it too. ``init-env`` gets no Pi
    argument: the installer is published apart from agent releases, and an
    older release fails on an argument it does not know."""
    body = _strip_powershell_comments(_function_body_text("Write-AgentEnv"))
    init_at = body.find("init-env --prefix")
    fresh_install_ends = body.find("return", init_at)
    first = body.find("Set-PiBinIfMissing")
    last = body.rfind("Set-PiBinIfMissing")
    assert -1 < init_at < first < fresh_install_ends < last, body
    assert "--pi-bin" not in _strip_powershell_comments(_read_install_ps1_text())

