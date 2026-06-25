"""Pins on the venv "bad interpreter" fix inside ``deploy/install.ps1``'s
``Install-Release`` (the Windows counterpart to
``test_install_sh_stage_release_sentinel.py``).

Two contracts the fix added to ``Install-Release``:

1. **Completion sentinel.** It must write ``releases/<version>/.installed``
   after the wheel ``pip install`` succeeds. The Windows supervisor
   (``deploy/windows_launcher.py``'s ``_is_release_complete`` /
   ``_resolve_release_python`` / ``_find_recovery_version``) and the
   canonical ``mobaide.agent.install_paths.is_release_complete`` treat a
   release as runnable only when it carries that sentinel OR the legacy
   ``mobaide-agent.exe`` console script. A fresh Windows install must drop
   the sentinel so it is recognised the same way the updater's releases
   are.

2. **Base-interpreter hardening (R1).** It must base the release venv off
   ``sys._base_executable`` (a ``$basePython`` resolved from ``$PythonExe``)
   and clear any active ``VIRTUAL_ENV`` / ``PYTHONHOME`` before
   ``-m venv``. Same root cause as the POSIX side: a release venv that
   symlinks a transient venv breaks once that venv moves, leaving a
   "bad interpreter" shim on ``pip`` / ``mobaide-agent``.

Pure AST / text pins on the source-of-truth file — the script is NEVER
executed (it self-elevates via UAC and installs software). All structural
assertions run through
``[System.Management.Automation.Language.Parser]::ParseFile`` (tokenise +
AST only). Auto-skipped where neither ``powershell.exe`` nor ``pwsh`` is
on PATH.
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
    """Invoke the resolved PowerShell host with a ``-Command`` script.

    ``-NoProfile`` / ``-NonInteractive`` keep host customisations out of
    stdout. ``encoding='utf-8', errors='replace'`` mirrors the sibling
    ``test_install_ps1.py`` rationale: PS 5.1 mis-decodes the UTF-8
    em-dashes in install.ps1's comments and the host re-emits mojibake; all
    assertions here match ASCII tokens, so replacement is harmless.
    """
    assert _PS is not None
    return subprocess.run(
        [_PS, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def _ps_quote(s: str) -> str:
    """Escape a string for a single-quoted PowerShell literal (``'`` -> ``''``)."""
    return s.replace("'", "''")


@cache
def _list_function_names() -> tuple[str, ...]:
    """All function names defined in ``deploy/install.ps1`` (AST walk)."""
    assert _DEPLOY_INSTALL_PS1.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_PS1}"
    )
    path_literal = _ps_quote(str(_DEPLOY_INSTALL_PS1))
    script = (
        "$tokens = $null; $errors = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{path_literal}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { foreach ($e in $errors) { Write-Error $e.Message }; exit 1 }; "
        "$funcs = $ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $true); "
        "foreach ($f in $funcs) { Write-Output $f.Name }"
    )
    proc = _run_ps(script)
    assert proc.returncode == 0, (
        f"function-name enumeration failed: stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


@cache
def _function_body_text(name: str) -> str:
    """Body extent text of a named function via AST (brace-safe slice)."""
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


def test_install_release_function_is_defined() -> None:
    """``Install-Release`` must exist (the function this module pins)."""
    funcs = _list_function_names()
    assert "Install-Release" in funcs, (
        f"expected function 'Install-Release' in {_DEPLOY_INSTALL_PS1}; "
        f"found functions: {funcs!r}. Main() stages every release through "
        f"it; the .installed sentinel + base-interpreter hardening live in "
        f"its body."
    )


def test_install_release_resolves_base_python() -> None:
    """``Install-Release`` must resolve ``$basePython`` via
    ``sys._base_executable`` and build the venv from it.

    Mirrors install.sh's ``resolve_base_python``: when ``$PythonExe`` is
    itself a venv, ``sys._base_executable`` is the real interpreter behind
    the shim. Basing the release venv off that stable interpreter is what
    keeps it from symlinking a transient venv whose later removal leaves a
    "bad interpreter" shebang on pip / mobaide-agent. A regression that
    reverts to ``& $PythonExe -m venv`` re-opens the bug on hosts running
    the installer from inside a venv.
    """
    body = _function_body_text("Install-Release")
    assert "sys._base_executable" in body, (
        f"Install-Release must read sys._base_executable to resolve the "
        f"stable interpreter behind a venv shim (R1 hardening, mirrors "
        f"install.sh resolve_base_python); not found in body:\n{body!r}"
    )
    assert re.search(r"\$basePython\b", body), (
        f"Install-Release must capture the resolved interpreter into "
        f"$basePython; not found in body:\n{body!r}"
    )
    # The venv-creation invocation must use $basePython, not $PythonExe.
    assert re.search(r"&\s+\$basePython\s+-m\s+venv\b", body), (
        f"Install-Release must create the release venv with "
        f"`& $basePython -m venv` (the stable base interpreter), NOT "
        f"`& $PythonExe -m venv` — the latter re-links the venv to a "
        f"possibly-transient interpreter and re-opens the bad-interpreter "
        f"bug. Body:\n{body!r}"
    )


def test_install_release_clears_virtualenv_before_venv() -> None:
    """``Install-Release`` must clear ``VIRTUAL_ENV`` / ``PYTHONHOME``
    before the ``-m venv`` call.

    Even with ``$basePython`` resolved, an inherited ``$env:VIRTUAL_ENV``
    can steer ``python -m venv`` to derive the new venv from the active
    venv. Removing those env vars (``Remove-Item Env:\\VIRTUAL_ENV``) is
    the second half of the R1 hardening; the unset must come BEFORE the
    venv is created, or it has no effect.
    """
    body = _function_body_text("Install-Release")
    assert "VIRTUAL_ENV" in body, (
        f"Install-Release must clear VIRTUAL_ENV for the release-venv "
        f"creation (R1 hardening); not found in body:\n{body!r}"
    )
    assert re.search(r"Remove-Item\s+Env:\\?VIRTUAL_ENV", body), (
        f"Install-Release must `Remove-Item Env:\\VIRTUAL_ENV` so an "
        f"active venv cannot steer `python -m venv`; not found in "
        f"body:\n{body!r}"
    )
    clear_at = body.find("VIRTUAL_ENV")
    venv_at = body.find("-m venv")
    assert venv_at != -1 and clear_at < venv_at, (
        f"Install-Release must clear VIRTUAL_ENV BEFORE `-m venv` (found "
        f"clear at offset {clear_at}, venv at offset {venv_at}); clearing "
        f"it afterwards leaves the venv already based off the active venv. "
        f"Body:\n{body!r}"
    )


def test_install_release_writes_installed_sentinel() -> None:
    """``Install-Release`` must write ``releases/<version>/.installed``.

    The completion sentinel is the canonical "runnable release" signal the
    Windows supervisor's ``_is_release_complete`` gates recovery on. A
    fresh Windows install must drop it (into the release dir, via the
    ``$releaseDir`` join) so it is recognised the same way the updater's
    releases are — without it, new installs rely solely on the legacy
    ``mobaide-agent.exe`` console-script fallback and diverge from the
    updater contract.
    """
    body = _function_body_text("Install-Release")
    assert ".installed" in body, (
        f"Install-Release must write the .installed completion sentinel; "
        f"not found in body:\n{body!r}"
    )
    # The sentinel must be joined onto the release dir, not the prefix
    # root (a prefix-global marker would mark every release complete).
    assert re.search(r"Join-Path\s+\$releaseDir\s+['\"]\.installed['\"]", body), (
        f"Install-Release must write the sentinel under "
        f"(Join-Path $releaseDir '.installed') — per-release, not "
        f"prefix-global; not found in body:\n{body!r}"
    )


def test_install_release_writes_sentinel_after_wheel_install() -> None:
    """The ``.installed`` write must come AFTER the wheel ``pip install``.

    The sentinel attests that pip AND the wheel are installed. If a
    regression moves the write above the ``$venvPip install ... $wheel``
    line, a crash between the two leaves a 'complete' marker on a release
    with no ``mobaide-agent.exe`` — exactly the half-built state the
    sentinel exists to exclude, which the supervisor would then run.
    """
    body = _function_body_text("Install-Release")
    # Anchor the wheel install on the `$venvPip install ... $wheel` call.
    wheel_match = re.search(r"\$venvPip\s+install[^\n]*\$wheel", body)
    sentinel_at = body.find(".installed")
    assert wheel_match is not None, (
        f"Install-Release must `& $venvPip install ... $wheel` (the wheel "
        f"install the sentinel attests to); not found in body:\n{body!r}"
    )
    assert sentinel_at != -1, (
        f"Install-Release must write the .installed sentinel; not found in "
        f"body:\n{body!r}"
    )
    assert wheel_match.start() < sentinel_at, (
        f"Install-Release must write .installed AFTER the wheel "
        f"`$venvPip install ... $wheel` (found wheel install at offset "
        f"{wheel_match.start()}, sentinel write at offset {sentinel_at}). "
        f"Writing it first would let a crash between the two leave a "
        f"'complete' marker on a release with no wheel. Body:\n{body!r}"
    )


def test_install_release_moves_existing_release_aside_not_delete() -> None:
    """``Install-Release`` must move an existing same-version release aside,
    not delete it before staging succeeds.

    The Windows mirror of install.sh's crash-safety fix: ``Main`` swaps
    ``current_version`` and (re)starts the service only after
    ``Install-Release`` returns, so deleting ``$releaseDir`` up front would leave
    a single-release install pointing at a missing/half-built dir if any
    download / checksum / extraction / venv / pip step fails. ``Install-Release``
    must instead move the live dir to a ``$releaseBackup`` and let the ``finally``
    restore it on failure (or discard it on success). Building into a fresh
    ``New-Item`` dir also keeps any stale completeness signal out of the rebuild
    window.
    """
    body = _function_body_text("Install-Release")
    move_aside = re.search(
        r"Move-Item\s+-Force\s+\$releaseDir\s+\$releaseBackup", body
    )
    assert move_aside is not None, (
        f"Install-Release must move an existing release aside via "
        f"`Move-Item -Force $releaseDir $releaseBackup` (preserving it for "
        f"restore) rather than deleting it up front; not found in body:\n{body!r}"
    )
    # No destructive removal of the live release BEFORE the move-aside. (The only
    # legitimate `Remove-Item ... $releaseDir` is the restore branch in finally,
    # textually after the move-aside.)
    pre = body[: move_aside.start()]
    assert not re.search(r"Remove-Item[^\n]*\$releaseDir\b", pre), (
        f"Install-Release must NOT Remove-Item $releaseDir before moving it "
        f"aside — that re-opens the destroy-before-stage brick. Body:\n{body!r}"
    )
    # Backup uses the hidden '.old-<v>-<pid>' scratch convention.
    assert re.search(r"\.old-\$version-\$PID", body), (
        f"the move-aside backup must use the '.old-<v>-<pid>' scratch name "
        f"(matching install.sh / the updater) so selectors skip it; not found "
        f"in body:\n{body!r}"
    )
    # Move-aside precedes the fresh-dir creation and the venv build.
    new_item_at = body.find("New-Item -ItemType Directory -Path $releaseDir")
    venv_at = body.find("-m venv")
    assert new_item_at != -1 and move_aside.start() < new_item_at, (
        f"the move-aside must precede the fresh New-Item dir creation; "
        f"body:\n{body!r}"
    )
    assert venv_at != -1 and move_aside.start() < venv_at


def test_install_release_restores_or_discards_backup_in_finally() -> None:
    """``Install-Release``'s ``finally`` must settle the moved-aside release.

    ``Write-Fail`` (``Write-Error; exit 1``) unwinds through ``finally`` — the
    same property the existing ``$tmp`` cleanup relies on — so on any
    pre-sentinel failure (``$staged`` still ``$false``) the live release is
    restored, and on success the backup is discarded. Without this, a staging
    failure would leave the install bricked with the live release stranded under
    its ``$releaseBackup`` name.
    """
    body = _function_body_text("Install-Release")
    # Success is signalled by $staged = $true right after the sentinel write.
    assert re.search(r"\$staged\s*=\s*\$true", body), (
        f"Install-Release must set $staged = $true after the .installed write "
        f"to mark a completed rebuild; not found in body:\n{body!r}"
    )
    # finally restores the live release on failure...
    assert re.search(r"Move-Item\s+-Force\s+\$releaseBackup\s+\$releaseDir", body), (
        f"Install-Release's finally must restore the live release via "
        f"`Move-Item -Force $releaseBackup $releaseDir` when staging fails; "
        f"not found in body:\n{body!r}"
    )
    # ...and the restore/discard branches on the $staged flag.
    assert re.search(r"if\s*\(\s*\$staged\s*\)", body), (
        f"Install-Release's backup settlement must branch on $staged (discard "
        f"on success, restore on failure); not found in body:\n{body!r}"
    )


def test_install_ps1_parses_cleanly() -> None:
    """``deploy/install.ps1`` must parse under the AST parser.

    Cheap insurance that the sentinel + base-python edits did not break the
    script (mismatched braces, malformed here-strings). Equivalent to
    ``bash -n`` for the PowerShell side.
    """
    path_literal = _ps_quote(str(_DEPLOY_INSTALL_PS1))
    script = (
        "$tokens = $null; $errors = $null; "
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{path_literal}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { "
        "  foreach ($e in $errors) { Write-Output $e.Message }; "
        "  exit 1 "
        "} else { "
        "  Write-Output 'PARSE_OK'; "
        "  exit 0 "
        "}"
    )
    proc = _run_ps(script)
    assert proc.returncode == 0, (
        f"PowerShell AST parse failed for {_DEPLOY_INSTALL_PS1}: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "PARSE_OK" in proc.stdout, (
        f"expected PARSE_OK token in stdout, got: stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
