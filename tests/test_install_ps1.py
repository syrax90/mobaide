"""Regression tests for ``deploy/install.ps1`` (Windows installer).

Parallel to ``tests/test_install_script_trap.py`` (which covers
``deploy/install.sh``). Two contracts are pinned here:

1. **Syntactic health.** The script must parse cleanly under the
   PowerShell AST parser — the Windows equivalent of ``bash -n``.
2. **Behavioural surface.** The unelevated-but-self-elevating flow and
   the two-stage Python 3.12 auto-install (``winget`` fast path →
   official python.org installer fallback) are the only entry path
   for the canonical one-liner (``irm … | iex``) from a normal,
   non-admin PowerShell window. Several small contracts hold that
   path together:

   * The historical ``#Requires -RunAsAdministrator`` directive is gone
     (its presence would short-circuit the elevation function before
     the user sees any of our own messaging).
   * ``Test-IsAdmin``, ``Assert-AdminOrElevate``, ``Find-Python``,
     ``Install-Python``, ``Install-PythonViaWinget``,
     ``Install-PythonFromPythonOrg``, ``Update-CurrentSessionPath``
     and ``Resolve-Python`` exist as functions in the script (not as
     inline blocks or arbitrary comments).
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
   * ``Install-PythonViaWinget`` invokes ``winget`` with the pinned
     ``Python.Python.3.12`` package id, and ``Install-Python``
     orchestrates the winget-then-python.org fallback in that order
     (regression guards on the auto-install path itself).
   * ``Install-PythonFromPythonOrg`` downloads a pinned 3.12.x
     installer (amd64 / arm64), runs it silently at machine scope,
     and Authenticode-verifies the signer subject ``Python Software
     Foundation`` before executing it as admin (defence-in-depth on
     top of TLS, closing the rogue-CA / python.org-compromise gap).
   * ``Find-Python`` filters out the ``WindowsApps`` App Execution
     Alias stub so the Store-trampoline reparse point never wins
     detection.

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

    ``encoding='utf-8', errors='replace'`` overrides ``text=True``'s default
    of ``locale.getpreferredencoding()`` (cp1252 on English Windows).
    PowerShell 5.1's ``Parser.ParseFile`` reads ``install.ps1`` (no BOM)
    using the system ANSI codepage rather than UTF-8, so a UTF-8 em-dash
    ``\\xe2\\x80\\x94`` in a comment is decoded as ``\\u00e2\\u20ac\\u201d``,
    and the host's UTF-8 ``[Console]::OutputEncoding`` then writes those
    chars back out as ``\\xc3\\xa2\\xe2\\x82\\xac\\xe2\\x80\\x9d``. Byte
    ``0x9d`` is undefined in cp1252, so the default ``text=True`` decode
    raises ``UnicodeDecodeError`` in subprocess's reader thread and the
    test sees ``proc.stdout=None``. ``errors='replace'`` substitutes ``?``
    for any byte that survives mis-decoding — all assertions in this file
    match ASCII tokens, so mojibake in non-ASCII regions is harmless.
    """
    assert _PS is not None
    return subprocess.run(
        [_PS, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
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


@cache
def _function_body_text(name: str) -> str:
    """Return the body extent text of a named function via AST.

    Equivalent to ``sed -n '/^foo()/,/^}$/p'`` in the bash test module,
    but uses the AST so brace-matching is robust and ``return``s
    inside nested scriptblocks do not derail the slice.

    ``@cache``-keyed on ``name`` because several tests query the same
    function body (``Install-PythonFromPythonOrg`` alone is read by six
    assertions). Without caching, every call spawns a fresh PowerShell
    host — 300-800 ms each on Windows — which scales linearly as the
    suite grows. The cache is safe: ``deploy/install.ps1`` does not
    change during a test session.
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
        "Install-PythonViaWinget",
        "Install-PythonFromPythonOrg",
        "Update-CurrentSessionPath",
        "Resolve-Python",
        "Invoke-PythonScript",
    ],
)
def test_required_function_is_defined(func_name: str) -> None:
    """Each function the elevation/Python bootstrap depends on must
    exist as a real function in ``install.ps1``.

    Found via AST walk so we are pinning the actual symbol, not a
    string that happens to occur in a comment or here-string. If any
    one is renamed or deleted, ``Main`` (or ``Install-Python``'s
    fallback orchestration) calls into the void and the install fails
    opaquely; this test surfaces it locally. The
    ``Install-PythonViaWinget`` / ``Install-PythonFromPythonOrg`` /
    ``Update-CurrentSessionPath`` triple is the two-stage fallback
    chain introduced to fix the winget-absent hosts (LTSC, Server,
    Sandbox, GPO-locked).
    """
    funcs = _list_function_names()
    assert func_name in funcs, (
        f"expected function {func_name!r} to be defined in "
        f"{_DEPLOY_INSTALL_PS1}; found functions: {funcs!r}. "
        f"Main() depends on the Test-IsAdmin / Assert-AdminOrElevate / "
        f"Find-Python / Install-Python / Resolve-Python contract; "
        f"Install-Python depends on Install-PythonViaWinget, "
        f"Install-PythonFromPythonOrg, and Update-CurrentSessionPath. "
        f"If any one is missing the unelevated `irm | iex` flow breaks."
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


def test_install_python_via_winget_invokes_winget_with_pinned_3_12() -> None:
    """``Install-PythonViaWinget`` must call ``winget`` for
    ``Python.Python.3.12``.

    Pinning the minor version (vs ``Python.Python.3``) is a deliberate
    choice: the agent's contract is "Python 3.12 or newer", so the lower
    bound is what the installer should resolve. A drift to a metapackage
    alias that later points at 3.14+ would silently install a version we
    have not verified the agent against.

    Also guards against an accidental stub-out of the winget path
    (e.g., a future refactor that drops the ``& winget`` call but leaves
    the function name in place — the orchestrator would then always
    fall through to the python.org installer, defeating the fast path
    on every modern Windows host).
    """
    body = _function_body_text("Install-PythonViaWinget")
    assert "winget" in body, (
        f"Install-PythonViaWinget must invoke winget; not found in "
        f"body:\n{body!r}"
    )
    assert "Python.Python.3.12" in body, (
        f"Install-PythonViaWinget must pin Python.Python.3.12 "
        f"explicitly (Python.Python.3 is rejected to avoid a future "
        f"metapackage drift to an unverified minor version). "
        f"Body:\n{body!r}"
    )


def test_install_python_orchestrates_winget_then_pythonorg() -> None:
    """``Install-Python`` must call ``Install-PythonViaWinget`` first
    and ``Install-PythonFromPythonOrg`` second.

    The two-stage fallback order matters: winget is the fast path on
    modern Windows 11 / 10 (a single command, no .exe download), and
    the python.org installer is the universal fallback (works on LTSC,
    Server, Sandbox, GPO-locked, and any winget-absent host). Reversing
    the order would always download a multi-MB .exe even when winget
    could have done the install in seconds. A future PR that drops
    one of the two calls is also a regression — both must remain in
    the orchestrator body.

    The check walks the function body text and asserts both names
    appear *and* the winget call appears before the python.org call.
    """
    body = _function_body_text("Install-Python")
    via_winget_at = body.find("Install-PythonViaWinget")
    from_pythonorg_at = body.find("Install-PythonFromPythonOrg")
    assert via_winget_at != -1, (
        f"Install-Python must reference Install-PythonViaWinget (the "
        f"winget fast path); not found in body:\n{body!r}"
    )
    assert from_pythonorg_at != -1, (
        f"Install-Python must reference Install-PythonFromPythonOrg "
        f"(the universal fallback for winget-absent hosts — LTSC, "
        f"Server, Sandbox, GPO-locked); not found in body:\n{body!r}"
    )
    assert via_winget_at < from_pythonorg_at, (
        f"Install-Python must call Install-PythonViaWinget BEFORE "
        f"Install-PythonFromPythonOrg (winget is the fast path; the "
        f"python.org download is the fallback). Found winget call at "
        f"offset {via_winget_at}, python.org call at offset "
        f"{from_pythonorg_at}. Body:\n{body!r}"
    )


def test_pythonorg_fallback_pins_3_12_x_url() -> None:
    """``Install-PythonFromPythonOrg`` must point at python.org's
    versioned ``ftp/python/3.12.<patch>/`` path.

    python.org has no stable "latest" alias — every release has its
    own versioned URL. Pinning a patch in source means:
    1. The URL is reproducible (no surprise patch-version drift in
       the install flow).
    2. A future patch bump is a single-line code change, reviewed in
       a PR rather than auto-applied at install time.

    The pinned version must be a real ``3.12.<n>`` patch so the agent
    contract ("Python 3.12+") is honoured on the fallback path.
    """
    body = _function_body_text("Install-PythonFromPythonOrg")
    assert "https://www.python.org/ftp/python/" in body, (
        f"Install-PythonFromPythonOrg must download from "
        f"python.org's versioned ftp/python/ path; not found in "
        f"body:\n{body!r}"
    )
    assert re.search(r'"3\.12\.\d+"', body), (
        f"Install-PythonFromPythonOrg must pin a concrete 3.12.<n> "
        f"patch version (python.org has no stable 'latest' alias); "
        f"not found in body:\n{body!r}"
    )


def test_pythonorg_fallback_uses_silent_machine_scope() -> None:
    """``Install-PythonFromPythonOrg`` must run the python.org
    installer silently at machine scope.

    Three flags pinned by this test:
    * ``/quiet`` — no UI, required for unattended ``irm | iex``.
    * ``InstallAllUsers=1`` — machine-scope install. Matches the
      prior ``winget --scope machine`` choice so the SCM service
      (LocalSystem) sees the interpreter at runtime. A per-user
      install would be invisible to LocalSystem and break the
      service launcher.
    * ``PrependPath=1`` — drops Python on system PATH so the
      ``Update-CurrentSessionPath`` refresh resolves it without a
      reboot.

    A regression on any of these turns the silent fallback into an
    interactive prompt, a per-user install (invisible to the service),
    or a no-PATH install (Find-Python returns null and the orchestrator
    hard-fails).
    """
    body = _function_body_text("Install-PythonFromPythonOrg")
    for flag in ("/quiet", "InstallAllUsers=1", "PrependPath=1"):
        assert flag in body, (
            f"Install-PythonFromPythonOrg must pass {flag!r} to the "
            f"python.org installer (silent / machine-scope / on-PATH "
            f"contract); not found in body:\n{body!r}"
        )


def test_pythonorg_fallback_supports_arm64() -> None:
    """``Install-PythonFromPythonOrg`` must detect ARM64 and select the
    matching python.org installer.

    python.org has shipped ``python-<ver>-arm64.exe`` since 3.11, and
    Windows-on-ARM (Snapdragon Surface, Project Volterra, Copilot+ PCs)
    is now common enough that an architecture-blind install — always
    downloading the amd64 .exe — would either fail outright on native
    ARM64 PowerShell or rely on the x64 emulator, which is the wrong
    interpreter to ship to a native ARM agent.

    The body must read ``$env:PROCESSOR_ARCHITECTURE`` (the canonical
    Windows arch probe) and reference both ``amd64`` and ``arm64`` so
    the URL composition can switch on it.
    """
    body = _function_body_text("Install-PythonFromPythonOrg")
    assert "PROCESSOR_ARCHITECTURE" in body, (
        f"Install-PythonFromPythonOrg must read "
        f"$env:PROCESSOR_ARCHITECTURE to pick the right .exe; not "
        f"found in body:\n{body!r}"
    )
    for arch in ("amd64", "arm64"):
        assert arch in body, (
            f"Install-PythonFromPythonOrg must reference {arch!r} (the "
            f"python.org URL distinguishes amd64 and arm64 builds); "
            f"not found in body:\n{body!r}"
        )


def test_pythonorg_fallback_verifies_authenticode_before_running() -> None:
    """``Install-PythonFromPythonOrg`` must Authenticode-verify the
    downloaded installer is signed by the Python Software Foundation
    *before* launching it as admin.

    Defense in depth on top of TLS: a rogue root CA on the host (e.g.,
    an enterprise SSL-inspection middlebox or a malware-installed
    root) lets a network attacker swap the .exe behind a valid-looking
    cert chain. Authenticode roots in Microsoft's certificate program,
    independent of the host's TLS trust store, so checking the signer
    subject closes the rogue-CA / python.org-compromise gap.

    The body must call ``Get-AuthenticodeSignature`` and reject any
    file that is not signed by ``Python Software Foundation``.
    Order matters: the verification must happen *after* the download
    and *before* the ``Start-Process`` that runs the installer with
    machine-scope privileges. A regression that moves the check to
    after the install (or drops it entirely) would re-open the gap
    that this pin closes.
    """
    body = _function_body_text("Install-PythonFromPythonOrg")
    assert "Get-AuthenticodeSignature" in body, (
        f"Install-PythonFromPythonOrg must Authenticode-verify the "
        f"downloaded installer before running it (defence-in-depth on "
        f"top of TLS — closes the rogue-CA / python.org-compromise "
        f"gap); Get-AuthenticodeSignature not found in body:\n{body!r}"
    )
    assert "Python Software Foundation" in body, (
        f"Install-PythonFromPythonOrg must pin the Authenticode signer "
        f"subject to 'Python Software Foundation' (a bare Status=Valid "
        f"check would accept any Authenticode-signed .exe served from "
        f"a compromised host); not found in body:\n{body!r}"
    )
    auth_at = body.find("Get-AuthenticodeSignature")
    start_at = body.find("Start-Process")
    assert auth_at != -1 and start_at != -1 and auth_at < start_at, (
        f"Install-PythonFromPythonOrg must call Get-AuthenticodeSignature "
        f"BEFORE Start-Process (running an unverified installer with "
        f"admin privileges is the exact behaviour this pin prevents). "
        f"Found Get-AuthenticodeSignature at offset {auth_at}, "
        f"Start-Process at offset {start_at}. Body:\n{body!r}"
    )


def test_pythonorg_fallback_authenticode_gate_rejects_with_status_valid() -> None:
    """``Install-PythonFromPythonOrg``'s Authenticode gate must pin
    ``Status -eq 'Valid'`` AND short-circuit the install (``return
    $false``) when verification fails — *between* the
    ``Get-AuthenticodeSignature`` call and the ``Start-Process`` that
    runs the installer with admin privileges.

    Complements ``test_pythonorg_fallback_verifies_authenticode_before_running``,
    which pins (a) the cmdlet's presence, (b) the signer-subject string,
    and (c) textual ordering of the cmdlet vs ``Start-Process``. Those
    three assertions all pass for a regression that

    * drops the ``Status`` check entirely and only matches the subject
      string (an attacker-signed certificate whose subject happens to
      match the pin would slip through — Authenticode reports
      ``Status='HashMismatch'`` / ``'NotTrusted'`` / ``'UnknownError'``
      for tampered or untrusted .exes, all of which the regression
      would accept), OR
    * keeps the verification call but drops the ``return $false`` from
      the rejection branch — ``Start-Process`` still runs because the
      cmdlet only *logs* the failure, defeating the whole point of the
      check.

    This test closes both gaps:

    1. The literal token ``'Valid'`` must appear in the function body
       so the gate pins the canonical Authenticode success state. The
       PowerShell ``SignatureStatus`` enum has ~10 values
       (``Valid``, ``UnknownError``, ``NotSigned``, ``HashMismatch``,
       ``NotTrusted``, ``NotSupportedFileFormat``, ``Incompatible``,
       ``HardeningRequired``, ``MissingFile``, ``Indeterminate``);
       only ``Valid`` means "the signature is real and matches the
       file's hash and chains to a trusted root". A regression to
       any of the others is silently insecure.
    2. A ``return $false`` (or equivalent control-flow exit) must
       appear textually *between* ``Get-AuthenticodeSignature`` and
       the ``Start-Process`` call. Textual ordering of cmdlet-vs-
       Start-Process is necessary but not sufficient: both calls can
       sit in textual order yet both run unconditionally if the
       rejection branch is a bare ``Write-Warn`` with no return.
    """
    body = _function_body_text("Install-PythonFromPythonOrg")
    auth_at = body.find("Get-AuthenticodeSignature")
    start_at = body.find("Start-Process")
    assert auth_at != -1 and start_at != -1 and auth_at < start_at, (
        f"Install-PythonFromPythonOrg must call Get-AuthenticodeSignature "
        f"before Start-Process; the cycle-2 ordering invariant is the "
        f"precondition for this stricter test. auth={auth_at} "
        f"start={start_at} body:\n{body!r}"
    )
    # Sub-contract 1: the canonical Authenticode success state is pinned.
    # Without 'Valid' literal, a regression could match against the
    # softer 'NotSigned' / 'Unknown' / 'UnknownError' states and silently
    # accept tampered or untrusted installers.
    assert re.search(r"['\"]Valid['\"]", body), (
        f"Install-PythonFromPythonOrg must pin the Authenticode status "
        f"to the literal 'Valid' (Status -eq 'Valid' / -ne 'Valid'). "
        f"PowerShell's SignatureStatus enum has ~10 values; only 'Valid' "
        f"means the signature is intact, the file's hash matches, and "
        f"the chain reaches a trusted Microsoft Authenticode root. A "
        f"regression that drops the Status pin would accept "
        f"'HashMismatch' (tampered), 'NotTrusted' (rogue chain), or "
        f"'UnknownError' (verification bug). Body:\n{body!r}"
    )
    # Sub-contract 2: the rejection branch short-circuits before
    # Start-Process. Textual ordering alone doesn't prove the gate
    # actually blocks — a bare Write-Warn with no `return $false` would
    # log the rejection and still run the unverified installer with
    # admin privileges. Search the slice strictly *between* the cmdlet
    # call and Start-Process for an exit statement; this guarantees the
    # rejection path bails out before Start-Process is reachable.
    between = body[auth_at:start_at]
    assert re.search(r"\breturn\s+\$false\b", between), (
        f"Install-PythonFromPythonOrg must short-circuit with "
        f"`return $false` between Get-AuthenticodeSignature and "
        f"Start-Process. Without an explicit return from the rejection "
        f"branch, a bare Write-Warn would log the failure and still run "
        f"the unverified .exe as admin — the exact behaviour this gate "
        f"exists to prevent. Slice between offsets {auth_at} and "
        f"{start_at}:\n{between!r}"
    )


def test_find_python_skips_windowsapps_stub() -> None:
    """``Find-Python`` must filter out the App Execution Alias stub
    under ``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\``.

    That stub is a reparse-point trampoline that opens the Microsoft
    Store UI when invoked unattended — it does not actually run Python.
    If ``Get-Command python`` resolves to the stub, the downstream
    ``& $python -c "..."`` calls (manifest signature verify, wheel
    staging) hang or error opaquely. Defensive guard: filter
    ``$exe.Source -like "*\\WindowsApps\\*"`` so the stub never wins
    the detection race against a real interpreter elsewhere on PATH.
    """
    body = _function_body_text("Find-Python")
    assert re.search(r"WindowsApps", body), (
        f"Find-Python must filter out the WindowsApps App Execution "
        f"Alias stub (the reparse-point trampoline that opens the "
        f"Microsoft Store and never runs Python in `irm | iex`); not "
        f"found in body:\n{body!r}"
    )


def test_install_ps1_does_not_pass_heredoc_to_python_dash_c() -> None:
    """install.ps1 must not invoke ``& $PythonExe -c $<variable>``.

    Reason: Windows PowerShell 5.1's native-argument quoting silently
    drops embedded ``"`` characters when wrapping a long argument for a
    native ``.exe``. ``CommandLineToArgvW`` then toggles in/out of
    quoted-region state at every stripped quote and joins the surrounding
    tokens, so a here-string line like::

        sys.exit(f"trust key is not a valid base64 ...: {exc}")

    arrives at python.exe as::

        sys.exit(ftrust key is not a valid base64 ...: {exc})

    and Python reports ``SyntaxError: '(' was never closed`` (its
    canonical error when a parse fails inside an open paren). PowerShell
    7.3+ defaults to a fixed argv-passing mode (PSNativeCommand-
    ArgumentPassing = 'Standard'), but the canonical install one-liner
    ``irm https://get.mobaide.com/install.ps1 | iex`` runs under whatever
    PowerShell the user has, and 5.1 is the default on every fresh
    Windows install — we cannot require 7.3+.

    The ``Invoke-PythonScript`` helper writes the script to a temp file
    and runs ``python <file>``, sidestepping argv-quoting entirely. Short
    inline ``& $cmd -c "<literal-no-quotes>"`` calls remain safe and are
    explicitly allowed (``Find-Python``'s version probe at line ~101,
    the manifest version extract at line ~368 — both single-line ASCII
    expressions whose Python uses only ``'`` for string literals).

    Regex pin: any occurrence of ``& $PythonExe -c $<word>`` is a
    regression. ``& $PythonExe -c "..."`` (string literal) and
    ``& $PythonExe $tmpScript ...`` (script-as-file, the fixed form
    inside the helper) are both untouched by this pattern.
    """
    text = _read_install_ps1_text()
    matches = re.findall(r"&\s+\$PythonExe\s+-c\s+\$\w+", text)
    assert not matches, (
        "install.ps1 passes a script via `& $PythonExe -c $<variable>`. "
        "Windows PowerShell 5.1 strips embedded `\"` characters from "
        "native-command argv, mangling any here-string Python script "
        "with f-strings, dict-key access, or double-quoted literals. "
        "Use Invoke-PythonScript (writes the script to a temp `.py` and "
        "runs `python <file>`) instead — it sidesteps argv-quoting "
        f"entirely and is host-version-agnostic. Offending matches: "
        f"{matches!r}"
    )


def test_pythonorg_fallback_treats_3010_as_success() -> None:
    """``Install-PythonFromPythonOrg`` must treat MSI exit code ``3010``
    as success-with-reboot-pending, NOT as a failure.

    ``3010`` is the documented MSI/Installer convention for
    "operation completed successfully, reboot pending". The python.org
    installer follows it. The rest of the agent install (venv creation,
    wheel install, service registration) still works after a 3010 —
    the only thing waiting on the reboot is some PATH propagation,
    which ``Update-CurrentSessionPath`` bridges in-process anyway.

    A regression that lumps 3010 in with "non-zero is failure" would
    cause every host whose installer signals a reboot to fall through
    to the final ``Write-Fail``, even though Python is actually
    installed and usable. The fallback would look broken on perfectly
    healthy installs.

    The body must reference ``3010`` literally and the comparison must
    short-circuit BEFORE the generic ``$proc.ExitCode -ne 0`` branch
    (otherwise the order is wrong and the function returns ``$false``
    on a successful-with-reboot install).
    """
    body = _function_body_text("Install-PythonFromPythonOrg")
    assert "3010" in body, (
        f"Install-PythonFromPythonOrg must special-case exit code "
        f"3010 (MSI success-with-reboot-pending); not found in "
        f"body:\n{body!r}"
    )
    # The 3010 branch must appear before the generic non-zero-failure
    # branch, otherwise 3010 is caught by `$proc.ExitCode -ne 0` first
    # and the function incorrectly returns $false.
    code_3010_at = body.find("3010")
    generic_failure_at = body.find("ExitCode -ne 0")
    assert generic_failure_at != -1, (
        f"Install-PythonFromPythonOrg must check $proc.ExitCode -ne 0 "
        f"for the generic failure case; not found in body:\n{body!r}"
    )
    assert code_3010_at < generic_failure_at, (
        f"Install-PythonFromPythonOrg must check 3010 BEFORE the "
        f"generic ExitCode -ne 0 branch (otherwise 3010 is misclassified "
        f"as a failure). Found 3010 at offset {code_3010_at}, generic "
        f"failure check at offset {generic_failure_at}. "
        f"Body:\n{body!r}"
    )


def test_pythonorg_fallback_cleans_up_temp_installer() -> None:
    """``Install-PythonFromPythonOrg`` must remove the downloaded
    installer in a ``finally`` block.

    The python.org installer is a ~30 MB ``.exe`` downloaded to
    ``$env:TEMP\\python-installer-<guid>.exe``. If cleanup is in the
    happy path (after the install succeeds) instead of ``finally``,
    every failed install — network drop mid-download, antivirus quarantine
    mid-install, ``Start-Process`` throwing — leaks a multi-MB blob.
    Over time, on a host that hits the fallback repeatedly, this
    silently consumes disk in ``%TEMP%``.

    The contract: a ``finally`` block exists in the body, and it
    contains a ``Remove-Item`` call against the temp file path.
    """
    body = _function_body_text("Install-PythonFromPythonOrg")
    # The AST collapses whitespace differently between hosts, so match
    # the `finally` keyword plus a `Remove-Item` in the same body — they
    # must both be present and `Remove-Item` must follow `finally` (the
    # finally block always comes after try in PowerShell syntax).
    assert re.search(r"\bfinally\b", body), (
        f"Install-PythonFromPythonOrg must wrap cleanup in a `finally` "
        f"block so a failed download/install does not leak the "
        f"~30 MB python-installer-<guid>.exe in $env:TEMP. "
        f"Body:\n{body!r}"
    )
    assert "Remove-Item" in body, (
        f"Install-PythonFromPythonOrg must call Remove-Item on the "
        f"downloaded installer (otherwise $env:TEMP accumulates a "
        f"multi-MB blob per failed install attempt). "
        f"Body:\n{body!r}"
    )
    finally_at = body.find("finally")
    remove_at = body.find("Remove-Item")
    assert finally_at < remove_at, (
        f"Install-PythonFromPythonOrg's Remove-Item must live inside "
        f"(or after) the `finally` block — if it sits in the try body "
        f"before `finally`, a thrown exception skips the cleanup and "
        f"the installer leaks. Found `finally` at offset {finally_at}, "
        f"`Remove-Item` at offset {remove_at}. Body:\n{body!r}"
    )


def test_pythonorg_fallback_observes_exit_code() -> None:
    """``Install-PythonFromPythonOrg`` must run the installer with
    ``Start-Process -Wait -PassThru`` so the exit code is observable.

    Without ``-Wait``, ``Start-Process`` returns before the installer
    finishes and ``$proc.ExitCode`` reads as ``$null`` (or whatever
    transient state the process is in). Without ``-PassThru``,
    ``Start-Process`` returns nothing at all and ``$proc`` is ``$null``.
    Either regression means the downstream
    ``if ($proc.ExitCode -eq 3010)`` / ``elif ($proc.ExitCode -ne 0)``
    branches never observe the real exit code:
      * 3010 (reboot pending) and real failures are both silently
        treated as success.
      * The function returns ``$true`` even on an installer crash.
      * ``Find-Python`` then fails post-install and the user sees a
        confusing "still not found after auto-install" error.

    Pinning both flags catches the regression at parse-time rather
    than at runtime on a winget-absent host.
    """
    body = _function_body_text("Install-PythonFromPythonOrg")
    for flag in ("-Wait", "-PassThru"):
        assert flag in body, (
            f"Install-PythonFromPythonOrg must launch the python.org "
            f"installer with {flag!r} so $proc.ExitCode reflects the "
            f"real outcome (3010 reboot-pending vs. real failure). "
            f"Without it, the function returns $true on a failed "
            f"install and the agent install proceeds into a broken "
            f"state. Body:\n{body!r}"
        )


def test_install_python_failure_message_drops_winget_only_phrasing() -> None:
    """``Install-Python``'s final hard-stop message must NOT claim
    ``winget is not available on PATH``.

    Pre-rewrite, the only failure mode was "no winget" and the message
    said so. Post-rewrite, the failure mode is "winget path AND
    python.org fallback both failed" — fundamentally different and
    much more informative. The plan's "Breaking changes" section
    explicitly calls out this message rewrite as user-visible.

    A regression that re-adds the old wording would mislead operators
    on a winget-present host whose winget install succeeded but whose
    python.org download then also failed — they'd be told to install
    winget, which they already have.

    Two parts of the contract:
      * The old, narrow phrasing is gone.
      * The new message mentions the two-stage failure (substring
        ``python.org`` appears, signalling that the fallback was
        attempted).
    """
    body = _function_body_text("Install-Python")
    assert "winget is not available on PATH" not in body or (
        # Allow the phrase only in the conditional log line for the
        # winget-absent branch, NOT inside the final Write-Fail
        # here-string. Find every occurrence and check that none of
        # them are inside the final Write-Fail block.
        body.count("winget is not available on PATH") <= 1
        and "Write-Fail" in body
        and body.find("winget is not available on PATH")
        < body.find("Write-Fail")
    ), (
        f"Install-Python's final Write-Fail must NOT claim 'winget "
        f"is not available on PATH' — the post-rewrite failure mode "
        f"is 'winget path AND python.org fallback both failed', and "
        f"the message text was rewritten accordingly (plan: 'Breaking "
        f"changes'). Body:\n{body!r}"
    )
    # The new message references the fallback explicitly so the
    # operator sees that two paths were tried, not one.
    assert "python.org" in body, (
        f"Install-Python's final Write-Fail must reference python.org "
        f"so the operator knows the fallback was attempted (the "
        f"manual remediation link also points at python.org). Not "
        f"found in body:\n{body!r}"
    )


def test_resolve_python_post_install_message_drops_winget_phrasing() -> None:
    """``Resolve-Python``'s post-install "still not found" message
    must say ``auto-install``, NOT ``winget install``.

    Pre-rewrite, this was ``"Python 3.12+ still not found after winget
    install."`` — accurate when winget was the only path. Post-rewrite,
    the auto-install could have been winget OR python.org, so the
    plan explicitly renames it to ``"... after auto-install."`` to
    avoid misleading operators about which path actually ran.

    A regression that keeps the old wording would surface on a
    winget-absent host that successfully ran the python.org fallback
    but where ``Find-Python`` still returned ``$null`` afterwards (e.g.
    PATH refresh didn't take). The operator would be told to debug
    winget, which never ran.
    """
    body = _function_body_text("Resolve-Python")
    assert "winget install" not in body, (
        f"Resolve-Python's post-install error message must say "
        f"'auto-install', not 'winget install' — the auto-install "
        f"could now be either winget or the python.org fallback "
        f"(plan: 'Resolve-Python (lines 137-149) needs a one-line "
        f"tweak'). Found 'winget install' literal in body:\n{body!r}"
    )
    assert "auto-install" in body, (
        f"Resolve-Python's post-install error message must mention "
        f"'auto-install' (the path-agnostic phrasing introduced by "
        f"the two-stage fallback rewrite). Not found in body:\n{body!r}"
    )
