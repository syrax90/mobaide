"""Tests for ``deploy/install.ps1`` (Windows installer).

Needs ``powershell.exe`` or ``pwsh``; the module skips without one. Most
checks run one function from the script (extracted with the PowerShell AST
parser); the rest are static pins for past bugs that a run on a non-Windows
host cannot reproduce.

Shared helpers for ``test_install_ps1_cli_shim.py`` and
``test_install_ps1_reinstall.py`` live here.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest


def _find_powershell() -> str | None:
    candidates: tuple[str, ...] = (
        ("powershell.exe", "pwsh") if sys.platform == "win32" else ("pwsh",)
    )
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


_PS = _find_powershell()

pytestmark = pytest.mark.skipif(
    _PS is None,
    reason="PowerShell (powershell.exe / pwsh) unavailable on this platform",
)


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_PS1 = _REPO_ROOT / "deploy" / "install.ps1"


def _run_ps(script: str) -> subprocess.CompletedProcess[str]:
    """``errors='replace'``: PowerShell 5.1 reads the BOM-less script as ANSI
    and can echo bytes that are not valid in the console code page."""
    assert _PS is not None
    return subprocess.run(
        [_PS, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def _ps_quote(s: str) -> str:
    """Escape ``s`` for a PowerShell single-quoted string."""
    return s.replace("'", "''")


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


@cache
def _read_install_ps1_text() -> str:
    return _DEPLOY_INSTALL_PS1.read_text(encoding="utf-8")


@cache
def _function_body_text(name: str) -> str:
    """Body of the PowerShell function ``name``, found through the AST."""
    path_literal = _ps_quote(str(_DEPLOY_INSTALL_PS1))
    fn_literal = _ps_quote(name)
    script = (
        "$tokens = $null; $errors = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{path_literal}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { exit 1 }; "
        f"$f = $ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq '{fn_literal}'}}, $true) | Select-Object -First 1; "
        f"if (-not $f) {{ Write-Error 'function {fn_literal} not found'; exit 2 }}; "
        "Write-Output $f.Body.Extent.Text"
    )
    proc = _run_ps(script)
    assert proc.returncode == 0, (
        f"function-body extraction for {name!r} failed: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return proc.stdout


_PS_BLOCK_COMMENT_RE = re.compile(r"<#.*?#>", re.DOTALL)
_PS_LINE_COMMENT_RE = re.compile(r"(?m)#.*$")


def _strip_powershell_comments(text: str) -> str:
    """Drop ``#`` and ``<# ... #>`` comments so pins match code only."""
    no_block = _PS_BLOCK_COMMENT_RE.sub("", text)
    return _PS_LINE_COMMENT_RE.sub("", no_block)


def test_install_ps1_parses_cleanly() -> None:
    path_literal = _ps_quote(str(_DEPLOY_INSTALL_PS1))
    script = (
        "$tokens = $null; $errors = $null; "
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{path_literal}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { foreach ($e in $errors) { Write-Output $e.Message }; exit 1 }"
    )
    proc = _run_ps(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pythonorg_installer_runs_only_with_a_valid_psf_signature() -> None:
    """The downloaded python.org installer runs elevated, so the Authenticode
    check (status ``Valid``, signer PSF) must return before ``Start-Process``
    — a rogue root CA on the host could otherwise swap the binary."""
    body = _strip_powershell_comments(
        _function_body_text("Install-PythonFromPythonOrg")
    )
    auth_at = body.find("Get-AuthenticodeSignature")
    start_at = body.find("Start-Process")
    assert -1 < auth_at < start_at, body
    gate = body[auth_at:start_at]
    assert re.search(r"['\"]Valid['\"]", gate), body
    assert "Python Software Foundation" in gate, body
    assert re.search(r"\breturn\s+\$false\b", gate), body


def test_install_ps1_does_not_pass_a_script_variable_to_python_dash_c() -> None:
    """Windows PowerShell 5.1 drops embedded ``"`` from long native
    arguments, so ``& $PythonExe -c $script`` arrives at Python as broken
    code. Multi-line scripts go through ``Invoke-PythonScript`` (a temp
    file) instead."""
    matches = re.findall(r"&\s+\$PythonExe\s+-c\s+\$\w+", _read_install_ps1_text())
    assert not matches


def test_install_release_does_not_download_with_python_urllib() -> None:
    """Python's ``urllib`` on Windows does no AIA chasing, so release
    downloads failed with ``CERTIFICATE_VERIFY_FAILED`` behind corporate
    proxies. ``Invoke-AgentDownload`` (Schannel) does the downloads."""
    assert "urllib.request" not in _function_body_text("Install-Release")


@pytest.mark.parametrize(
    ("url", "accepted"),
    [
        ("https://h.test/dl/mobaide_agent-1.2.3-py3-none-any.whl", True),
        ("https://h.test/dl/mobaide_agent-1.2.3.tar.gz", False),
        ("https://h.test/dl/agent.whl", False),
        pytest.param(
            "file:///tmp/a\\..\\mobaide_agent-1.2.3-py3-none-any.whl",
            False,
            marks=pytest.mark.skipif(
                sys.platform == "win32", reason="'\\' is a separator on Windows"
            ),
            id="backslash-in-basename",
        ),
    ],
)
def test_get_wheel_basename_accepts_only_a_plain_pep427_name(
    tmp_path, url, accepted
) -> None:
    result = _run_ps_file(
        tmp_path,
        ("Write-Fail", "Get-WheelBasename"),
        f"\"NAME=$(Get-WheelBasename -WheelUrl '{_ps_quote(url)}')\"\n",
    )

    if accepted:
        assert result.returncode == 0, result.stdout + result.stderr
        assert _values(result.stdout)["NAME"] == url.rsplit("/", 1)[1]
    else:
        assert result.returncode == 1, result.stdout
        assert "manifest wheel_url" in result.stderr
        assert "NAME" not in _values(result.stdout)
