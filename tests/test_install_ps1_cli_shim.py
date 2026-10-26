"""Pins on the ``mobaide-agent`` command that ``deploy/install.ps1`` puts on PATH.

The generated ``.cmd`` runs against a fake prefix whose "release" is a renamed
copy of ``cmd.exe``, so ``/c exit 7`` and ``/c set`` show what the shim passed
on. ``Add-UserPathEntry`` runs against a throwaway ``HKCU\\Software`` subkey that
the test deletes; the real ``HKCU\\Environment`` is never opened.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from test_install_ps1 import (
    _DEPLOY_INSTALL_PS1,
    _PS,
    _function_body_text,
    _ps_quote,
    _strip_powershell_comments,
)


pytestmark = pytest.mark.skipif(
    _PS is None,
    reason="PowerShell (powershell.exe / pwsh) unavailable on this platform",
)

_WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="runs a .cmd and writes HKCU"
)

_INSTALL_URL = "https://get.example.test/install.ps1"


def _run_ps_file(
    tmp_path: Path, functions: tuple[str, ...], body: str
) -> subprocess.CompletedProcess[str]:
    """Define ``functions`` from ``install.ps1`` and run ``body`` after them.

    The script goes through a UTF-8-with-BOM file so Windows PowerShell 5.1
    neither re-quotes it on the command line nor reads it as ANSI.
    """
    assert _PS is not None
    names = ", ".join(f"'{_ps_quote(n)}'" for n in functions)
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$tokens = $null; $errors = $null\n"
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{_ps_quote(str(_DEPLOY_INSTALL_PS1))}', [ref]$tokens, [ref]$errors)\n"
        f"foreach ($name in @({names})) {{\n"
        "    $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)\n"
        "    . ([ScriptBlock]::Create($fn.Extent.Text))\n"
        "}\n"
        + body
    )
    path = tmp_path / "harness.ps1"
    path.write_text(script, encoding="utf-8-sig")
    return subprocess.run(
        [_PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(path)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def _values(stdout: str) -> dict[str, str]:
    return dict(re.findall(r"^([A-Z]+)=(.*?)\s*$", stdout, re.M))


def _install_cli_shim(
    tmp_path: Path,
    prefix: Path,
    *,
    install_url: str = _INSTALL_URL,
    path_entry_fails: bool = False,
) -> subprocess.CompletedProcess[str]:
    add_path = (
        "throw 'registry is read-only'"
        if path_entry_fails
        else "$Script:PathEntries += ,$Directory"
    )
    body = (
        f"$Script:InstallUrl = '{_ps_quote(install_url)}'\n"
        "$Script:PathEntries = @()\n"
        f"function Add-UserPathEntry {{ param([string] $Directory) {add_path} }}\n"
        f"$cli = Install-CliShim -Prefix '{_ps_quote(str(prefix))}'\n"
        "\"TYPE=$($cli.GetType().Name)\"\n"
        "\"SHIM=$($cli.Path)\"\n"
        "\"COMMAND=$($cli.Command)\"\n"
        "\"ENTRIES=$($Script:PathEntries -join '|')\"\n"
    )
    return _run_ps_file(tmp_path, ("Write-Info", "Write-Warn", "Install-CliShim"), body)


def _make_release(prefix: Path, version: str) -> None:
    scripts = prefix / "releases" / version / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(
        Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe",
        scripts / "mobaide-agent.exe",
    )


def _run_cmd_shim(shim: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k.upper() != "MOBAIDE_AGENT_PREFIX"}
    return subprocess.run(
        [str(shim), *args], capture_output=True, text=True, env=env
    )


@pytest.fixture
def prefix(tmp_path) -> Path:
    # The default prefix is under "Program Files"; keep the space.
    prefix = tmp_path / "Mobaide Agent"
    prefix.mkdir()
    return prefix


@_WINDOWS_ONLY
class TestInstallCliShim:
    def test_writes_an_ascii_crlf_shim_and_registers_its_directory(
        self, tmp_path, prefix
    ):
        result = _install_cli_shim(tmp_path, prefix)

        assert result.returncode == 0, result.stderr
        shim = prefix / "bin" / "mobaide-agent.cmd"
        assert _values(result.stdout) == {
            "TYPE": "Hashtable",
            "SHIM": str(shim),
            "COMMAND": "mobaide-agent",
            "ENTRIES": str(prefix / "bin"),
        }
        data = shim.read_bytes()
        assert data.isascii()
        assert data.count(b"\n") == data.count(b"\r\n")
        assert b"mobaide-agent CLI shim" in data

    def test_falls_back_to_the_full_shim_path_when_path_cannot_be_updated(
        self, tmp_path, prefix
    ):
        result = _install_cli_shim(tmp_path, prefix, path_entry_fails=True)

        assert result.returncode == 0, result.stderr
        shim = prefix / "bin" / "mobaide-agent.cmd"
        values = _values(result.stdout)
        assert values["TYPE"] == "Hashtable"
        assert values["COMMAND"] == f"& '{shim}'"
        assert shim.is_file()
        assert "registry is read-only" in result.stdout + result.stderr


@_WINDOWS_ONLY
class TestGeneratedCmdShim:
    @pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
    def test_runs_the_current_release_and_returns_its_exit_code(
        self, tmp_path, prefix, newline
    ):
        _make_release(prefix, "0.4.0")
        (prefix / "current_version").write_bytes(b"0.4.0" + newline)
        _install_cli_shim(tmp_path, prefix)

        result = _run_cmd_shim(prefix / "bin" / "mobaide-agent.cmd", "/d", "/c", "exit", "7")

        assert result.returncode == 7, result.stderr

    def test_exports_the_prefix_it_lives_in(self, tmp_path, prefix):
        _make_release(prefix, "0.4.0")
        (prefix / "current_version").write_text("0.4.0\n", encoding="ascii")
        _install_cli_shim(tmp_path, prefix)

        result = _run_cmd_shim(
            prefix / "bin" / "mobaide-agent.cmd", "/d", "/c", "set", "MOBAIDE_AGENT_PREFIX"
        )

        assert result.returncode == 0, result.stderr
        assert f"MOBAIDE_AGENT_PREFIX={prefix}" in result.stdout.splitlines()

    @pytest.mark.parametrize("version", [None, "9.9.9"], ids=["no-version", "release-gone"])
    def test_no_install_exits_9009_with_the_reinstall_command(
        self, tmp_path, prefix, version
    ):
        _make_release(prefix, "0.4.0")
        if version is not None:
            (prefix / "current_version").write_text(f"{version}\n", encoding="ascii")
        _install_cli_shim(tmp_path, prefix)

        result = _run_cmd_shim(prefix / "bin" / "mobaide-agent.cmd", "show-qr")

        assert result.returncode == 9009
        assert f"no Mobaide agent is installed at {prefix}." in result.stderr
        assert f"irm {_INSTALL_URL} | iex" in result.stderr

    def test_install_url_with_cmd_metacharacters_is_echoed_literally(
        self, tmp_path, prefix
    ):
        url = "https://get.example.test/install.ps1?a=1&b=%PATH%"
        _install_cli_shim(tmp_path, prefix, install_url=url)

        result = _run_cmd_shim(prefix / "bin" / "mobaide-agent.cmd", "show-qr")

        assert result.returncode == 9009
        assert f"irm {url} | iex" in result.stderr


_DIRECTORY = r"C:\Program Files\MobaideAgent\bin"


def _add_user_path_entry(
    tmp_path: Path, seed: tuple[str, str] | None
) -> dict[str, str]:
    """Run ``Add-UserPathEntry`` against a throwaway HKCU subkey seeded with
    ``(kind, raw Path value)`` and report the value, kind and broadcasts."""
    subkey = f"Software\\MobaideInstallerTest-{uuid.uuid4().hex}"
    seed_line = (
        f"$k.SetValue('Path', '{_ps_quote(seed[1])}', "
        f"[Microsoft.Win32.RegistryValueKind]::{seed[0]})\n"
        if seed
        else ""
    )
    body = (
        "$Script:Broadcasts = 0\n"
        "function Send-EnvironmentChange { $Script:Broadcasts++ }\n"
        "function Update-CurrentSessionPath { }\n"
        "$env:MOBAIDE_TEST_ROOT = 'C:\\Program Files\\MobaideAgent'\n"
        f"$sub = '{subkey}'\n"
        "try {\n"
        "    $k = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($sub)\n"
        f"    {seed_line}"
        "    $k.Close()\n"
        f"    Add-UserPathEntry -Directory '{_ps_quote(_DIRECTORY)}' -SubKey $sub\n"
        "    $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($sub)\n"
        "    \"KIND=$($k.GetValueKind('Path'))\"\n"
        "    \"RAW=$($k.GetValue('Path', '', 'DoNotExpandEnvironmentNames'))\"\n"
        "    \"BROADCASTS=$Script:Broadcasts\"\n"
        "    $k.Close()\n"
        "} finally {\n"
        "    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($sub, $false)\n"
        "}\n"
    )
    result = _run_ps_file(tmp_path, ("Add-UserPathEntry",), body)
    assert result.returncode == 0, result.stdout + result.stderr
    return _values(result.stdout)


@_WINDOWS_ONLY
class TestAddUserPathEntry:
    def test_creates_an_expandable_path_when_there_is_none(self, tmp_path):
        assert _add_user_path_entry(tmp_path, None) == {
            "KIND": "ExpandString",
            "RAW": _DIRECTORY,
            "BROADCASTS": "1",
        }

    @pytest.mark.parametrize(
        ("kind", "raw", "expected"),
        [
            ("String", r"C:\Tools;C:\Go\bin;", rf"C:\Tools;C:\Go\bin;{_DIRECTORY}"),
            (
                "ExpandString",
                r"%USERPROFILE%\bin;C:\Tools",
                rf"%USERPROFILE%\bin;C:\Tools;{_DIRECTORY}",
            ),
        ],
        ids=["reg-sz", "reg-expand-sz"],
    )
    def test_appends_and_keeps_the_raw_value_and_its_kind(
        self, tmp_path, kind, raw, expected
    ):
        assert _add_user_path_entry(tmp_path, (kind, raw)) == {
            "KIND": kind,
            "RAW": expected,
            "BROADCASTS": "1",
        }

    @pytest.mark.parametrize(
        "existing",
        [
            r"C:\PROGRAM FILES\MOBAIDEAGENT\BIN" "\\",
            r'"C:\Program Files\MobaideAgent\bin"',
            r"%MOBAIDE_TEST_ROOT%\bin",
        ],
        ids=["case-and-trailing-slash", "quoted", "env-var"],
    )
    def test_equivalent_entry_is_left_alone(self, tmp_path, existing):
        raw = rf"C:\Tools;{existing};C:\Go\bin"

        assert _add_user_path_entry(tmp_path, ("ExpandString", raw)) == {
            "KIND": "ExpandString",
            "RAW": raw,
            "BROADCASTS": "0",
        }


def test_main_installs_the_shim_before_the_qr_step_uses_it() -> None:
    """``Show-PairingQrFromService -Cli`` and ``Write-Completion -Command``
    both take ``Install-CliShim``'s result, so it must run first."""
    body = _strip_powershell_comments(_function_body_text("Main"))
    shim_at = body.find("Install-CliShim")
    assert shim_at != -1, f"Main must call Install-CliShim. Body:\n{body}"
    assert body.find("Install-Service") < shim_at, body
    assert shim_at < body.find("Show-PairingQrFromService"), body
    assert shim_at < body.find("Write-Completion"), body
