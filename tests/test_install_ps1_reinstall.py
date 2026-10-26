"""Pins on re-running ``deploy/install.ps1`` over an existing install.

Covers the UAC relaunch, which must run the same installer the operator
started rather than the published one, and ``Stop-AgentProcesses``, which
must clear every process running from the prefix so a same-version reinstall
can move the release dir aside.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_install_ps1 import _PS, _function_body_text, _ps_quote, _strip_powershell_comments
from test_install_ps1_cli_shim import _run_ps_file, _values


pytestmark = pytest.mark.skipif(
    _PS is None,
    reason="PowerShell (powershell.exe / pwsh) unavailable on this platform",
)

_INSTALL_URL = "https://get.example.test/install.ps1"


def _elevated_command(tmp_path: Path, installer_path: str) -> str:
    body = (
        f"$Script:InstallUrl = '{_ps_quote(_INSTALL_URL)}'\n"
        f"$Script:InstallerPath = '{_ps_quote(installer_path)}'\n"
        "$env:MOBAIDE_AGENT_PREFIX = 'C:\\It''s Mobaide'\n"
        "\"CMD=$(Get-ElevatedInstallCommand)\"\n"
    )
    result = _run_ps_file(tmp_path, ("Get-ElevatedInstallCommand",), body)
    assert result.returncode == 0, result.stdout + result.stderr
    return _values(result.stdout)["CMD"]


class TestGetElevatedInstallCommand:
    def test_a_file_run_relaunches_that_file(self, tmp_path):
        command = _elevated_command(tmp_path, r"D:\work tree\it's\install.ps1")

        assert command == (
            "$env:MOBAIDE_AGENT_PREFIX = 'C:\\It''s Mobaide'; "
            "& 'D:\\work tree\\it''s\\install.ps1'"
        )

    def test_irm_iex_fetches_the_install_url_again(self, tmp_path):
        command = _elevated_command(tmp_path, "")

        assert command == (
            "$env:MOBAIDE_AGENT_PREFIX = 'C:\\It''s Mobaide'; "
            f"irm '{_INSTALL_URL}' | iex"
        )


def test_assert_admin_or_elevate_runs_the_elevated_install_command() -> None:
    body = _strip_powershell_comments(_function_body_text("Assert-AdminOrElevate"))
    assert "Get-ElevatedInstallCommand" in body, body
    assert "$urlEscaped" not in body, body


def _start_sleeper(directory: Path) -> subprocess.Popen[bytes]:
    directory.mkdir(parents=True)
    exe = directory / "sleeper.exe"
    shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32" / "PING.EXE", exe)
    return subprocess.Popen(
        [str(exe), "-n", "120", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="stops real Windows processes")
def test_stop_agent_processes_stops_only_processes_under_the_prefix(tmp_path):
    prefix = tmp_path / "Mobaide Agent"
    inside = _start_sleeper(prefix / "releases" / "0.4.0" / "venv" / "Scripts")
    # Shares the prefix as a string prefix but is a different directory.
    outside = _start_sleeper(tmp_path / "Mobaide Agent-other")
    try:
        body = (
            "$Script:ServiceName = 'MobaideAgentTestNoSuchTask'\n"
            f"$stopped = Stop-AgentProcesses -Prefix '{_ps_quote(str(prefix))}'\n"
            "\"STOPPED=$stopped\"\n"
        )
        result = _run_ps_file(tmp_path, ("Write-Info", "Stop-AgentProcesses"), body)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _values(result.stdout)["STOPPED"] == "True"
        assert inside.wait(timeout=10) is not None
        assert outside.poll() is None
    finally:
        for proc in (inside, outside):
            if proc.poll() is None:
                proc.kill()
                proc.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="queries Windows processes")
def test_stop_agent_processes_reports_nothing_stopped_on_an_idle_prefix(tmp_path):
    body = (
        "$Script:ServiceName = 'MobaideAgentTestNoSuchTask'\n"
        f"$stopped = Stop-AgentProcesses -Prefix '{_ps_quote(str(tmp_path / 'idle'))}'\n"
        "\"STOPPED=$stopped\"\n"
    )
    result = _run_ps_file(tmp_path, ("Write-Info", "Stop-AgentProcesses"), body)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _values(result.stdout)["STOPPED"] == "False"


def test_install_release_stops_the_agent_before_moving_the_release_aside() -> None:
    body = _strip_powershell_comments(_function_body_text("Install-Release"))
    stop_at = body.find("Stop-AgentProcesses")
    assert stop_at != -1, body
    assert stop_at < body.find("Move-Item -Force $releaseDir $releaseBackup"), body


def test_install_service_stops_leftover_processes_before_starting_the_task() -> None:
    body = _strip_powershell_comments(_function_body_text("Install-Service"))
    stop_at = body.find("Stop-AgentProcesses")
    assert stop_at != -1, body
    assert stop_at < body.find("Register-ScheduledTask"), body
    assert stop_at < body.find("Start-ScheduledTask"), body
