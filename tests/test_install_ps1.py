"""Regression tests for ``deploy/install.ps1`` (Windows installer).

Parallel to ``tests/test_install_script_trap.py`` (which covers
``deploy/install.sh``). Two contracts are pinned here:

1. **Syntactic health.** The script must parse cleanly under the
   PowerShell AST parser — the Windows equivalent of ``bash -n``.
2. **Behavioural surface.** The unelevated-but-self-elevating flow and
   the ``winget``-based Python 3.12 bootstrap are the only entry path
   for the canonical one-liner (``irm … | iex``) from a normal,
   non-admin PowerShell window. Several small contracts hold that path
   together:

   * The historical ``#Requires -RunAsAdministrator`` directive is gone
     (its presence would short-circuit the elevation function before
     the user sees any of our own messaging).
   * ``Test-IsAdmin``, ``Assert-AdminOrElevate``, ``Find-Python``,
     ``Install-Python`` and ``Resolve-Python`` exist as functions in
     the script (not as inline blocks or arbitrary comments).
   * The old ``Test-Python`` body is gone — the rename is complete, no
     dead code remains.
   * The new env-var hooks ``MOBAIDE_AGENT_INSTALL_URL`` and
     ``MOBAIDE_AGENT_SKIP_PYTHON_INSTALL`` are wired through the
     script (regression guard on the env-var contract documented in
     ``docs/versioning-and-updates.md``).
   * ``MOBAIDE_AGENT_SKIP_PYTHON_INSTALL`` is in the elevation
     passthrough array — otherwise a CI host invoking the one-liner
     non-admin would silently lose its opt-out flag across the UAC
     boundary, defeating the whole point of the env var.
   * ``Install-Python`` actually invokes ``winget`` with the pinned
     ``Python.Python.3.12`` package id (regression guard on the
     auto-install path itself).

Auto-skipped where neither ``powershell.exe`` nor ``pwsh`` is on PATH
(any host without PowerShell — most Linux/macOS CI without
PowerShell Core installed). The PowerShell-on-Linux installation
provides ``pwsh``; Windows ships ``powershell.exe`` natively.

Side effects are scrupulously avoided: the script is never *executed*
(it would self-elevate via UAC and install software). All assertions
work off ``[System.Management.Automation.Language.Parser]::ParseFile``,
which only tokenises and builds the AST — no command runs.
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
    """Locate a PowerShell host capable of running the AST parser.

    Prefer Windows ``powershell.exe`` on Windows (always present on
    supported targets), fall back to ``pwsh`` (PowerShell Core) on any
    platform. Skip the whole module if neither is available — there is
    no point parsing PowerShell without a parser.

    Structured as a single loop over a platform-derived candidate tuple
    so Pyright's ``sys.platform`` narrowing doesn't flag the cross-OS
    fallback as unreachable on whichever platform happens to be
    type-checking.
    """
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

    ``-NoProfile`` and ``-NonInteractive`` guard against host
    customisations (user ``$PROFILE``, prompts) that would otherwise
    pollute stdout and make assertions flaky on developer machines.
    """
    assert _PS is not None
    return subprocess.run(
        [_PS, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
    )


def _ps_quote(s: str) -> str:
    """Escape a string for embedding inside a single-quoted PS literal.

    PowerShell single-quoted strings treat ``'`` as the only special
    character; doubling it (``''``) is the canonical escape. Backslashes
    and ``$`` are literal inside single quotes, which is exactly what
    we want for Windows paths.
    """
    return s.replace("'", "''")


@cache
def _list_function_names() -> tuple[str, ...]:
    """Return all function names defined in ``deploy/install.ps1``.

    Uses ``Parser.ParseFile`` + an AST walk for
    ``FunctionDefinitionAst`` nodes — more faithful than a regex on the
    file text (a function-name string in a docstring or comment would
    not match, and an actual function would not be missed). Cached so
    each assertion does not re-spawn a PowerShell process.
    """
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
def _read_install_ps1_text() -> str:
    """File text of ``deploy/install.ps1`` (UTF-8, decoded once)."""
    assert _DEPLOY_INSTALL_PS1.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_PS1}"
    )
    return _DEPLOY_INSTALL_PS1.read_text(encoding="utf-8")


def _function_body_text(name: str) -> str:
    """Return the body extent text of a named function via AST.

    Equivalent to ``sed -n '/^foo()/,/^}$/p'`` in the bash test module,
    but uses the AST so brace-matching is robust and ``return``s
    inside nested scriptblocks do not derail the slice.
    """
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


def test_install_ps1_parses_cleanly() -> None:
    """``deploy/install.ps1`` must parse under the AST parser.

    Equivalent to ``bash -n install.sh``: catches syntax breakage
    locally before push (mismatched braces, malformed here-strings,
    rogue backticks at line ends, etc.).
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


def test_requires_runasadministrator_directive_is_removed() -> None:
    """``#Requires -RunAsAdministrator`` must NOT be active in install.ps1.

    Regression guard on the elevation rewrite: if a future PR re-adds
    the directive, ``Assert-AdminOrElevate`` is dead code (the
    directive fires first and aborts with a terse error before any of
    our own messaging runs, which is exactly the UX the rewrite was
    designed to remove).

    Checked via ``ScriptBlockAst.ScriptRequirements.IsElevationRequired``
    rather than a text regex on the file — the parser correctly ignores
    the literal token inside the explanatory comment block (which would
    fool a naive regex). When no requirements directive is declared at
    all, ``ScriptRequirements`` is ``$null``; when the directive is
    real, ``IsElevationRequired`` is ``$true``.
    """
    path_literal = _ps_quote(str(_DEPLOY_INSTALL_PS1))
    script = (
        "$tokens = $null; $errors = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{path_literal}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { foreach ($e in $errors) { Write-Error $e.Message }; exit 1 }; "
        "$req = $ast.ScriptRequirements; "
        "if ($null -eq $req) { Write-Output 'NO_REQUIREMENTS' } "
        "else { Write-Output ('IsElevationRequired=' + $req.IsElevationRequired) }"
    )
    proc = _run_ps(script)
    assert proc.returncode == 0, (
        f"ScriptRequirements probe failed: stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    out = proc.stdout.strip()
    # Two acceptable states: no requirements block at all, OR a
    # requirements block that explicitly does not assert elevation.
    # Anything else (including a future "True") is a regression.
    assert out in {"NO_REQUIREMENTS", "IsElevationRequired=False"}, (
        f"#Requires -RunAsAdministrator must not be active in "
        f"{_DEPLOY_INSTALL_PS1}; AST reports {out!r}. The elevation "
        f"rewrite (Assert-AdminOrElevate) replaces it; re-adding the "
        f"directive makes the unelevated `irm | iex` one-liner abort "
        f"with a terse parser error before any of our own messages run."
    )


@pytest.mark.parametrize(
    "func_name",
    [
        "Test-IsAdmin",
        "Assert-AdminOrElevate",
        "Find-Python",
        "Install-Python",
        "Resolve-Python",
    ],
)
def test_required_function_is_defined(func_name: str) -> None:
    """Each function the elevation/Python bootstrap depends on must
    exist as a real function in ``install.ps1``.

    Found via AST walk so we are pinning the actual symbol, not a
    string that happens to occur in a comment or here-string. If any
    one of these is renamed or deleted, ``Main`` calls into the void
    and the install fails opaquely; this test surfaces it locally.
    """
    funcs = _list_function_names()
    assert func_name in funcs, (
        f"expected function {func_name!r} to be defined in "
        f"{_DEPLOY_INSTALL_PS1}; found functions: {funcs!r}. "
        f"Main() depends on the Test-IsAdmin / Assert-AdminOrElevate / "
        f"Find-Python / Install-Python / Resolve-Python contract — if "
        f"any one is missing the unelevated `irm | iex` flow breaks."
    )


def test_old_test_python_function_is_removed() -> None:
    """The pre-refactor ``Test-Python`` must NOT be defined.

    The rename split its responsibility into ``Find-Python`` (silent
    detect) and ``Resolve-Python`` (orchestrator that may invoke
    ``Install-Python``). Leaving the old definition behind would mean
    two competing entry points and confusing dead code; the plan
    explicitly states ``Test-Python`` is deleted entirely after the
    rename.
    """
    funcs = _list_function_names()
    assert "Test-Python" not in funcs, (
        f"Test-Python must be removed from {_DEPLOY_INSTALL_PS1} after "
        f"the Find-Python / Resolve-Python rename; the plan deletes it "
        f"entirely (no callers remain). Found functions: {funcs!r}."
    )


@pytest.mark.parametrize(
    "env_var",
    [
        "MOBAIDE_AGENT_INSTALL_URL",
        "MOBAIDE_AGENT_SKIP_PYTHON_INSTALL",
    ],
)
def test_new_env_var_contract_is_wired(env_var: str) -> None:
    """The two new env-var hooks must appear in ``install.ps1``.

    These are the env-var contract documented in
    ``docs/versioning-and-updates.md`` (and exposed for self-hosted
    distributions / CI hosts respectively). A plain substring check is
    enough — both are referenced both as ``$env:NAME`` reads and as
    literals in the elevation passthrough array, so multiple
    occurrences are expected. If the file does not mention the name
    at all, the contract is silently broken.
    """
    text = _read_install_ps1_text()
    assert env_var in text, (
        f"expected env-var contract {env_var!r} to appear in "
        f"{_DEPLOY_INSTALL_PS1}; not found. This breaks the documented "
        f"contract in docs/versioning-and-updates.md and silently "
        f"disables the corresponding hook ($Script:InstallUrl re-fetch "
        f"URL / Python auto-install opt-out)."
    )


def test_skip_python_install_is_preserved_across_uac_elevation() -> None:
    """``MOBAIDE_AGENT_SKIP_PYTHON_INSTALL`` must be in the elevation
    passthrough array literal inside ``Assert-AdminOrElevate``.

    Cross-elevation env-var passthrough is implemented as a static
    array of names (``$passthrough = @( 'MOBAIDE_AGENT_PREFIX', … )``)
    that the function iterates to build a ``$env:NAME = '...'`` prefix
    for the elevated re-fetch command. If the new opt-out flag is not
    in that array, a CI / locked-down host running the one-liner from
    a non-admin shell would silently lose its opt-out across the UAC
    boundary — the elevated child re-fetches without the flag set,
    auto-installs Python anyway, defeating the whole point of the
    env var.
    """
    body = _function_body_text("Assert-AdminOrElevate")
    # Match the array literal (single- or double-quoted, in any order)
    # so a future reformat that line-wraps the array does not flake.
    assert re.search(
        r"['\"]MOBAIDE_AGENT_SKIP_PYTHON_INSTALL['\"]", body
    ), (
        f"MOBAIDE_AGENT_SKIP_PYTHON_INSTALL must appear in the "
        f"$passthrough array inside Assert-AdminOrElevate so the "
        f"opt-out survives the UAC re-launch. Function body:\n{body!r}"
    )


def test_install_python_invokes_winget_with_pinned_3_12() -> None:
    """``Install-Python`` must call ``winget`` for ``Python.Python.3.12``.

    Pinning the minor version (vs ``Python.Python.3``) is a deliberate
    choice from the plan: the agent's contract is "Python 3.12 or
    newer", so the lower bound is what the installer should resolve.
    A drift to a metapackage alias that later points at 3.14+ would
    silently install a version we have not verified the agent against.

    Also guards against an accidental stub-out of the auto-install
    path (e.g., a future refactor that leaves only the ``Write-Fail``
    error message and forgets to actually invoke winget).
    """
    body = _function_body_text("Install-Python")
    assert "winget" in body, (
        f"Install-Python must invoke winget; not found in body:\n{body!r}"
    )
    assert "Python.Python.3.12" in body, (
        f"Install-Python must pin Python.Python.3.12 explicitly (the "
        f"plan rejects Python.Python.3 to avoid a future metapackage "
        f"drift to an unverified minor version). Body:\n{body!r}"
    )
