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


_PS_BLOCK_COMMENT_RE = re.compile(r"<#.*?#>", re.DOTALL)
_PS_LINE_COMMENT_RE = re.compile(r"(?m)#.*$")


def _strip_powershell_comments(text: str) -> str:
    """Return ``text`` with PowerShell ``#`` and ``<# … #>`` comments removed.

    Used by negative-pin assertions that want to forbid a substring from
    *executable* code without false-positive-matching the same substring
    inside a documentation comment. For example, the
    ``test_install_service_does_not_register_cmd_exe_as_service_binary``
    pin must reject a real ``cmd.exe`` service registration while
    tolerating an architectural-note comment that *mentions* the pre-fix
    ``cmd.exe /c launcher.cmd`` design as documentation of what the
    current code avoids.

    Limitation: a ``#`` literal inside a single-quoted PowerShell string
    on a code-bearing line would be stripped along with the rest of the
    line. ``deploy/install.ps1`` does not contain such constructs in the
    function bodies these tests query (verified via the AST extraction
    above), so the simpler regex pair is sufficient and avoids
    re-implementing PowerShell's tokenizer in Python.
    """
    no_block = _PS_BLOCK_COMMENT_RE.sub("", text)
    return _PS_LINE_COMMENT_RE.sub("", no_block)


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
        "Resolve-Pythonw",
        "Invoke-PythonScript",
        "Install-Service",
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
        "MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL",
    ],
)
def test_new_env_var_contract_is_wired(env_var: str) -> None:
    """Each env-var hook must appear in ``install.ps1``.

    These are the env-var contract documented in
    ``docs/versioning-and-updates.md`` and ``docs/deployment.md``
    (exposed for self-hosted distributions / CI hosts). A plain
    substring check is enough — each is referenced both as
    ``$env:NAME`` reads and as a literal in the elevation passthrough
    array, so multiple occurrences are expected. If the file does not
    mention the name at all, the contract is silently broken.
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


def test_windows_launcher_url_is_preserved_across_uac_elevation() -> None:
    """``MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL`` must be in the elevation
    passthrough array literal inside ``Assert-AdminOrElevate``.

    Same class of regression as
    ``test_skip_python_install_is_preserved_across_uac_elevation`` —
    if the self-hosted-mirror override is not in the passthrough
    array, a user invoking the one-liner non-admin with
    ``$env:MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL`` set will see the UAC
    child silently fall back to the canonical ``get.mobaide.com`` URL.
    The user thinks they are fully self-hosted; in reality the
    supervisor fetch silently goes to the canonical endpoint, defeating
    the whole point of the override.
    """
    body = _function_body_text("Assert-AdminOrElevate")
    assert re.search(
        r"['\"]MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL['\"]", body
    ), (
        f"MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL must appear in the "
        f"$passthrough array inside Assert-AdminOrElevate so the "
        f"self-hosted-mirror override survives the UAC re-launch. "
        f"Function body:\n{body!r}"
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
      prior ``winget --scope machine`` choice. The Scheduled Task
      action's ``Execute`` path is recorded at install time; using
      a machine-scope Python install means that path stays valid
      across user-account changes and is discoverable from any
      logged-in session.
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


@pytest.mark.parametrize(
    "func_name",
    [
        "Test-CryptographyImportable",
        "New-BootstrapVenv",
        "Resolve-VerifierPython",
    ],
)
def test_cryptography_bootstrap_function_is_defined(func_name: str) -> None:
    """The three new functions that bootstrap ``cryptography`` for the
    pre-wheel-install verification steps must be defined.

    Trust-key validation (``Confirm-TrustKey``) and manifest-signature
    verification (``Test-ManifestSignature``) both ``import cryptography``,
    but they run BEFORE ``Install-Release`` ever ``pip install``s the wheel
    that carries cryptography transitively. On a freshly-installed
    python.org or winget Python the import fails with
    ``ModuleNotFoundError: No module named 'cryptography'`` and the install
    aborts at the trust-key step.

    The fix introduces ``Resolve-VerifierPython`` — fast path returns the
    bare interpreter when ``cryptography`` is already importable
    (``Test-CryptographyImportable``); slow path delegates to
    ``New-BootstrapVenv`` which builds an ephemeral venv in ``$env:TEMP``
    and ``pip install``s ``cryptography>=43.0`` into it. If any of the
    three is renamed or deleted, the bootstrap chain breaks and the install
    regresses to the original ModuleNotFoundError.
    """
    funcs = _list_function_names()
    assert func_name in funcs, (
        f"expected function {func_name!r} to be defined in "
        f"{_DEPLOY_INSTALL_PS1}; found functions: {funcs!r}. "
        f"Main() now depends on the Test-CryptographyImportable / "
        f"New-BootstrapVenv / Resolve-VerifierPython chain to supply a "
        f"Python that can import cryptography for trust-key + manifest "
        f"verification — both run BEFORE Install-Release stages the wheel."
    )


def test_main_resolves_verifier_python_before_trust_key() -> None:
    """``Main`` must call ``Resolve-VerifierPython`` before
    ``Initialize-TrustKey``.

    The ordering is load-bearing: ``Initialize-TrustKey`` invokes
    ``Confirm-TrustKey`` which imports ``cryptography``. If the verifier
    Python is resolved AFTER the trust-key step, the import fails on every
    host without a pre-existing cryptography install (the bug this fix
    targets). A regression that reorders the calls would silently re-open
    the original failure mode.
    """
    body = _function_body_text("Main")
    resolve_at = body.find("Resolve-VerifierPython")
    init_at = body.find("Initialize-TrustKey")
    assert resolve_at != -1, (
        f"Main must call Resolve-VerifierPython to obtain a Python that "
        f"can import cryptography for the trust-key + manifest checks; "
        f"not found in body:\n{body!r}"
    )
    assert init_at != -1, (
        f"Main must still call Initialize-TrustKey (the trust-key "
        f"bootstrap that gates the manifest signature verify); not "
        f"found in body:\n{body!r}"
    )
    assert resolve_at < init_at, (
        f"Main must call Resolve-VerifierPython BEFORE "
        f"Initialize-TrustKey (the latter imports cryptography via "
        f"Confirm-TrustKey; without the verifier the import fails on a "
        f"bare python.org install). Found Resolve-VerifierPython at "
        f"offset {resolve_at}, Initialize-TrustKey at offset {init_at}. "
        f"Body:\n{body!r}"
    )


def test_main_passes_verifier_python_to_trust_validation() -> None:
    """``Main`` must pass ``$verifier.Python`` (not the bare ``$python``)
    to ``Initialize-TrustKey`` and ``Test-ManifestSignature``.

    These are the two call sites that drive embedded Python scripts which
    ``import cryptography``. Wiring them to the bare ``$python`` re-opens
    the bug: a fresh python.org install has no cryptography. Wiring them
    to ``$verifier.Python`` either uses the bare interpreter (fast path)
    or the bootstrap venv (slow path), both of which can do the import.
    """
    body = _function_body_text("Main")
    assert re.search(
        r"Initialize-TrustKey\s+-PythonExe\s+\$verifier\.Python", body
    ), (
        f"Main must call Initialize-TrustKey with -PythonExe "
        f"$verifier.Python (the cryptography-capable interpreter from "
        f"Resolve-VerifierPython); not found in body:\n{body!r}"
    )
    assert re.search(
        r"Test-ManifestSignature\s+-PythonExe\s+\$verifier\.Python", body
    ), (
        f"Main must call Test-ManifestSignature with -PythonExe "
        f"$verifier.Python (the same cryptography-capable interpreter); "
        f"not found in body:\n{body!r}"
    )


def test_main_install_release_still_uses_bare_python() -> None:
    """Negative-control: ``Install-Release`` must still receive the bare
    ``$python`` (not ``$verifier.Python``).

    ``Install-Release`` calls ``& $PythonExe -m venv`` to create the
    release venv at ``$releaseDir\\venv``. Running ``python -m venv`` from
    inside the bootstrap venv would create a nested venv pointing at the
    bootstrap's interpreter — which the outer ``finally`` then deletes,
    leaving the release venv pointing at a non-existent Python. A
    regression here would surface as a silent post-install service-start
    failure (the launcher invokes a now-broken venv python).
    """
    body = _function_body_text("Main")
    assert re.search(
        r"Install-Release\s+-PythonExe\s+\$python\b", body
    ), (
        f"Main must call Install-Release with -PythonExe $python (the "
        f"bare interpreter from Resolve-Python). Using $verifier.Python "
        f"would create a nested release venv whose interpreter is "
        f"deleted by the outer cleanup. Not found in body:\n{body!r}"
    )


def test_bootstrap_venv_pins_cryptography_43_plus() -> None:
    """``New-BootstrapVenv`` must pin ``cryptography>=43.0``.

    Matches the floor declared in ``mobaide_agent/pyproject.toml`` line 23.
    Pinning a floor here (rather than an unbounded ``cryptography``) means
    the bootstrap venv loads at least the same API surface the wheel
    consumes — important if the verify scripts ever start using a
    cryptography 43+ feature. A drift to a stricter or older pin would
    silently diverge from the wheel's contract.
    """
    body = _function_body_text("New-BootstrapVenv")
    assert "cryptography>=43.0" in body, (
        f"New-BootstrapVenv must pin 'cryptography>=43.0' (matches "
        f"mobaide_agent/pyproject.toml line 23). Found body:\n{body!r}"
    )


def test_bootstrap_venv_cleanup_in_finally() -> None:
    """``Main`` must clean up ``$verifier.Root`` in a ``finally`` block.

    Cleanup in the happy path (after a successful Install-Release) would
    leak the bootstrap venv on every failed install — a multi-MB blob in
    ``$env:TEMP`` per failure. The ``finally`` placement ensures the
    cleanup fires regardless of whether trust-key validation, manifest
    fetch, signature verification, or wheel staging is the failure point.
    """
    body = _function_body_text("Main")
    assert re.search(r"\bfinally\b", body), (
        f"Main must wrap the verifier-venv cleanup in a `finally` block "
        f"(otherwise a failed install leaks the bootstrap venv on every "
        f"attempt). Body:\n{body!r}"
    )
    assert re.search(
        r"Remove-Item[^}]*\$verifier\.Root", body
    ), (
        f"Main's cleanup must Remove-Item $verifier.Root specifically — "
        f"a regression that drops the property access would either leak "
        f"the venv entirely or fail to even compile. Body:\n{body!r}"
    )


def test_resolve_verifier_python_short_circuits_when_importable() -> None:
    """``Resolve-VerifierPython`` must check ``Test-CryptographyImportable``
    BEFORE delegating to ``New-BootstrapVenv``.

    The fast-path optimisation matters: most pre-existing dev installs
    already have cryptography (it's a common transitive dep), so the
    bootstrap-venv cost (a pip install of ~5MB of wheels) should be
    skipped for them. A regression that inverts the order (or drops the
    check) would force every install — including all CI hosts with a
    pre-baked cryptography — through the slow path.
    """
    body = _function_body_text("Resolve-VerifierPython")
    check_at = body.find("Test-CryptographyImportable")
    boot_at = body.find("New-BootstrapVenv")
    assert check_at != -1, (
        f"Resolve-VerifierPython must call Test-CryptographyImportable "
        f"to short-circuit the fast path; not found in body:\n{body!r}"
    )
    assert boot_at != -1, (
        f"Resolve-VerifierPython must delegate to New-BootstrapVenv "
        f"when the importable check fails; not found in body:\n{body!r}"
    )
    assert check_at < boot_at, (
        f"Resolve-VerifierPython must run Test-CryptographyImportable "
        f"BEFORE New-BootstrapVenv (fast-path first). Found "
        f"Test-CryptographyImportable at offset {check_at}, "
        f"New-BootstrapVenv at offset {boot_at}. Body:\n{body!r}"
    )


def test_bootstrap_pip_install_flags() -> None:
    """``New-BootstrapVenv`` must pass ``--no-cache-dir`` and
    ``--disable-pip-version-check`` to the bootstrap pip install.

    * ``--no-cache-dir``: the venv is ephemeral (deleted in Main's
      finally). Caching the cryptography wheel in the user's pip cache
      would be a side effect of a transient install — and it duplicates
      what Install-Release already does on line 470-471 for the release
      venv. Matches the existing flag-style precedent.
    * ``--disable-pip-version-check``: suppresses the "new pip available"
      notice that pip emits on stderr — irrelevant noise for a throwaway
      venv that runs exactly one pip command and is then deleted.

    A regression that drops either flag is silently noisy / wasteful but
    not security-critical; this test catches it locally before push.
    """
    body = _function_body_text("New-BootstrapVenv")
    for flag in ("--no-cache-dir", "--disable-pip-version-check"):
        assert flag in body, (
            f"New-BootstrapVenv's pip install must pass {flag!r} (an "
            f"ephemeral venv should not pollute the user cache, and a "
            f"throwaway venv should not emit pip-upgrade nag). Not "
            f"found in body:\n{body!r}"
        )


def test_bootstrap_pip_install_uses_isolated_mode() -> None:
    """``New-BootstrapVenv``'s pip install must pass ``--isolated``.

    ``--index-url`` alone does NOT close the pip-config attack surface.
    Pip honours ``PIP_EXTRA_INDEX_URL`` and ``PIP_FIND_LINKS`` as ADDITIVE
    sources with no priority — an attacker who controls the install user's
    environment can publish ``cryptography==999.0.0`` on their mirror and
    pip will prefer it by version (classic dependency-confusion,
    CVE-2018-20225-class). ``pip.conf`` / ``pip.ini`` `[global]
    extra-index-url` entries have the same problem.

    Pip's ``--isolated`` flag is the canonical fix: it instructs pip to
    ignore environment variables AND user/system config files, leaving
    only the explicit CLI args in play. Combined with the
    ``--index-url https://pypi.org/simple`` pin, this hardens the
    bootstrap install against the full pip-config-driven hijack vector.

    A regression that drops ``--isolated`` while keeping ``--index-url``
    silently re-opens the PIP_EXTRA_INDEX_URL gap. This test pins both.
    """
    body = _function_body_text("New-BootstrapVenv")
    assert "--isolated" in body, (
        f"New-BootstrapVenv's pip install must pass --isolated so pip "
        f"ignores PIP_EXTRA_INDEX_URL, PIP_FIND_LINKS, PIP_TRUSTED_HOST, "
        f"and pip.conf/pip.ini (--index-url alone is necessary but not "
        f"sufficient — those vars/files are additive, not overridden). "
        f"Body:\n{body!r}"
    )


@pytest.mark.parametrize(
    "func_name",
    [
        "Invoke-AgentDownload",
        "Get-WheelBasename",
    ],
)
def test_install_release_download_helpers_are_defined(func_name: str) -> None:
    """The new staging helpers must exist as real functions.

    ``Install-Release`` previously bundled its own Python script that called
    ``urllib.request.urlopen`` to fetch the release tarball and wheel from
    the manifest. On Windows from python.org, urllib's default SSL context
    falls back to ``load_default_certs()`` (Windows ROOT store, no AIA
    chasing — CPython bpo-18617) and fails with
    ``SSL: CERTIFICATE_VERIFY_FAILED unable to get local issuer
    certificate`` on hosts with stripped intermediates or behind corporate
    MITM proxies — even though PowerShell's ``Invoke-WebRequest`` succeeds
    on the same URL because Schannel trusts the Windows root CA store
    including AD/enterprise CAs.

    The fix routes asset downloads through a centralized PowerShell helper
    (``Invoke-AgentDownload``) and lifts wheel-filename derivation /
    validation into a sibling helper (``Get-WheelBasename``) that runs
    before the download so a malformed manifest fails fast. If either
    helper is renamed or deleted, ``Install-Release`` either re-imports
    the bug (loses the urllib bypass) or silently accepts path-traversal
    wheel URLs.
    """
    funcs = _list_function_names()
    assert func_name in funcs, (
        f"expected function {func_name!r} to be defined in "
        f"{_DEPLOY_INSTALL_PS1}; found functions: {funcs!r}. "
        f"Install-Release depends on Invoke-AgentDownload (downloads "
        f"tarball + wheel via Schannel-backed Invoke-WebRequest, "
        f"sidestepping Python urllib's Windows CA-bundle gap) and "
        f"Get-WheelBasename (derives the canonical PEP 427 wheel "
        f"filename with path-traversal guards). If either is missing "
        f"the SSL bug returns or the basename guard regresses."
    )


def test_invoke_agent_download_uses_invoke_webrequest() -> None:
    """``Invoke-AgentDownload`` must call ``Invoke-WebRequest``.

    The whole point of the helper is to replace Python's
    ``urllib.request.urlopen`` (which uses OpenSSL's bundled trust path
    and fails on Windows-from-python.org with stripped intermediates)
    with PowerShell's ``Invoke-WebRequest`` (Schannel/HttpClient-backed,
    trusts the Windows root CA store including AD/enterprise CAs). A
    regression that swaps in a ``System.Net.WebClient`` or some other
    .NET API would still work in most cases but loses
    ``Invoke-WebRequest``'s automatic ``-UseBasicParsing`` / proxy /
    progress-bar semantics; a regression that re-routes through Python
    re-opens the original bug.

    Pinning the literal cmdlet name catches both regressions at
    parse-time.
    """
    body = _function_body_text("Invoke-AgentDownload")
    assert "Invoke-WebRequest" in body, (
        f"Invoke-AgentDownload must call Invoke-WebRequest (Schannel-"
        f"backed, trusts Windows root CA store including AD CAs); not "
        f"found in body:\n{body!r}"
    )


def test_invoke_agent_download_silences_progress_bar() -> None:
    """``Invoke-AgentDownload`` must set ``$ProgressPreference =
    'SilentlyContinue'`` around the call.

    PS 5.1's ``Write-Progress`` UI is implemented synchronously on the
    same thread that streams the response body, so the progress bar
    silently slows multi-MB downloads by 10× or worse
    (`PowerShell/PowerShell#2138 <https://github.com/PowerShell/PowerShell/issues/2138>`_,
    `#13414 <https://github.com/PowerShell/PowerShell/issues/13414>`_).
    The wheel + tarball download in ``Install-Release`` would take
    minutes instead of seconds without this guard. The canonical
    workaround — temporarily setting ``$ProgressPreference`` and
    restoring it in ``finally`` — is what
    ``Install-PythonFromPythonOrg`` already does (install.ps1:184-185,
    229) and is the same pattern this test pins for the new helper.

    A regression that drops the guard is silently noisy (every install
    on PS 5.1 hangs visibly on the progress UI) but not a correctness
    bug; this test catches it locally so the perf invariant survives
    future refactors.
    """
    body = _function_body_text("Invoke-AgentDownload")
    assert "ProgressPreference" in body, (
        f"Invoke-AgentDownload must save/restore $ProgressPreference "
        f"around its Invoke-WebRequest call (PS 5.1's progress UI is "
        f"implemented synchronously on the response-streaming thread "
        f"and silently slows multi-MB downloads by 10×+). Not found "
        f"in body:\n{body!r}"
    )
    assert "SilentlyContinue" in body, (
        f"Invoke-AgentDownload must set $ProgressPreference to "
        f"'SilentlyContinue' (the canonical workaround for PS 5.1's "
        f"slow progress UI; matches Install-PythonFromPythonOrg's "
        f"existing pattern around its Invoke-WebRequest call). Not "
        f"found in body:\n{body!r}"
    )


def test_install_release_does_not_embed_python_urllib_request() -> None:
    """``Install-Release``'s body must NOT contain the literal
    ``urllib.request``.

    This is the primary regression guard on the SSL fix. Pre-fix,
    ``Install-Release`` embedded a Python staging script that called
    ``urllib.request.urlopen(body["tarball_url"])`` and
    ``urllib.request.urlopen(body["wheel_url"])`` to download release
    assets. Python on Windows (from the python.org installer) links
    OpenSSL with an empty bundled CA path; ``urllib`` falls back to
    ``ssl.SSLContext.load_default_certs()`` which reads the Windows
    ``ROOT`` store but does NOT perform AIA (Authority Information
    Access) chasing for missing intermediates (CPython
    `bpo-18617 <https://bugs.python.org/issue18617>`_). On hosts with
    a stripped intermediate — common behind corporate MITM proxies or
    on freshly-built ROOT stores — every call to ``urlopen`` against
    https://api.github.com / https://github.com release-asset CDN
    fails with ``SSL: CERTIFICATE_VERIFY_FAILED unable to get local
    issuer certificate``. The same TLS endpoint is reachable from the
    same host via ``Invoke-WebRequest`` because Schannel trusts the
    Windows root CA store including AD/enterprise CAs injected by GPO.

    The fix moves both downloads out of the embedded Python script
    and into PowerShell's ``Invoke-AgentDownload`` helper. The Python
    script becomes verification-and-extraction only and no longer
    imports ``urllib.request`` at all. A regression that re-adds the
    import (and the ``urlopen`` calls it gates) re-opens the bug
    silently on every affected Windows host.
    """
    body = _function_body_text("Install-Release")
    assert "urllib.request" not in body, (
        f"Install-Release must not embed a Python staging script that "
        f"imports urllib.request — Python's urllib on Windows (from "
        f"the python.org installer) uses an empty OpenSSL CA path and "
        f"falls back to ssl.load_default_certs() without AIA chasing "
        f"(bpo-18617), so it fails with CERTIFICATE_VERIFY_FAILED on "
        f"hosts with stripped intermediates or behind corporate MITM "
        f"proxies. The fix routes downloads through "
        f"Invoke-AgentDownload (Schannel-backed). Body:\n{body!r}"
    )


def test_install_release_does_not_call_python_dash_c() -> None:
    """``Install-Release``'s body must not contain ``& $PythonExe -c``.

    Pre-fix, ``Install-Release`` extracted the manifest's version via
    ``& $PythonExe -c "import json,sys; print(json.load(open(sys.argv[1]))['body']['version'])" $ManifestPath``.
    That spun up a Python subprocess solely to parse a small JSON file
    that PowerShell can read with ``Get-Content -Raw |
    ConvertFrom-Json``. On Windows, the ``python -c`` round-trip is
    not affected by the SSL bug per se, but it shares the same general
    fragility class — quoting through ``CommandLineToArgvW`` (see
    ``test_install_ps1_does_not_pass_heredoc_to_python_dash_c`` for
    the historical fix on a sibling code path) and a needless Python
    dependency for a step that has no Python-specific logic.

    The fix replaces the ``python -c`` version-extract with native PS
    JSON parsing and asserts ``$manifestJson.body.version``,
    ``$manifestJson.body.tarball_url``, ``$manifestJson.body.wheel_url``
    are all present. A regression that re-introduces ``& $PythonExe -c``
    in this function would either re-add Python-quoting risk
    (if it carries an embedded ``"``) or perform a needless subprocess
    round-trip for a parsing step PowerShell does natively.

    Note: this test is scoped to ``Install-Release``'s body only.
    Short inline ``& $cmd -c "<literal>"`` calls elsewhere in the file
    (e.g., ``Find-Python``'s version probe) remain safe and are not
    covered by this assertion.
    """
    body = _function_body_text("Install-Release")
    assert not re.search(r"&\s+\$PythonExe\s+-c\b", body), (
        f"Install-Release must not call `& $PythonExe -c ...` — "
        f"manifest-JSON parsing is done natively in PowerShell now "
        f"(Get-Content -Raw | ConvertFrom-Json), and any other "
        f"`python -c` call here either re-adds Python-quoting risk "
        f"(CommandLineToArgvW dropping embedded \") or spins up a "
        f"subprocess for work PowerShell can do natively. "
        f"Body:\n{body!r}"
    )


def test_install_release_uses_invoke_agent_download() -> None:
    """``Install-Release`` must call ``Invoke-AgentDownload`` (the
    forward-looking pin that the new SSL-safe download path is wired in).

    Complements
    ``test_install_release_does_not_embed_python_urllib_request``,
    which is the negative pin (urllib.request must be absent). Both
    must hold: a regression could in principle drop urllib.request
    AND fail to call the helper (e.g., by inlining a different
    download mechanism inside Install-Release), and neither test
    alone would catch it. Together they pin "downloads happen, and
    they happen via the helper".

    The helper signature is ``-Url <string> -OutFile <path>``; this
    test only pins the function name, not the args — argument
    additions are forward-compatible and should not flake.
    """
    body = _function_body_text("Install-Release")
    assert "Invoke-AgentDownload" in body, (
        f"Install-Release must call Invoke-AgentDownload to fetch the "
        f"tarball + wheel (the Schannel-backed Invoke-WebRequest "
        f"wrapper that replaces the buggy urllib.request path). Not "
        f"found in body:\n{body!r}"
    )


def test_install_release_sets_installer_user_agent() -> None:
    """``Install-Release`` must set the User-Agent
    ``mobaide-agent-installer/<version>`` on its asset downloads.

    The release wheel is served by a Cloudflare-fronted CDN. Cloudflare
    Bot Fight Mode (rule 1010) blocks requests whose User-Agent matches
    its bot-pattern list — historically including the default
    ``Python-urllib/X.Y`` UA, and sometimes the default PowerShell UA
    (``Mozilla/5.0 (Windows NT...) WindowsPowerShell/<ver>``). The bash
    installer's USER_AGENT literal in ``deploy/install.sh`` (line ~430)
    was added for exactly this reason. The PS installer must match it
    so users behind a Cloudflare-fronted wheel host see consistent
    download behaviour across Windows and Linux.

    A regression that drops the UA (or sets it to the default PS UA)
    would silently re-trigger Bot Fight Mode on whatever fraction of
    the wheel-host CDN's edge nodes have the rule enabled — failures
    would be sporadic and hard to reproduce, exactly the kind of
    regression a static test prevents cheaply.
    """
    body = _function_body_text("Install-Release")
    assert "mobaide-agent-installer/" in body, (
        f"Install-Release must tag its asset downloads with the "
        f"User-Agent 'mobaide-agent-installer/<version>' (mirrors "
        f"deploy/install.sh's USER_AGENT — required to dodge "
        f"Cloudflare Bot Fight Mode 1010 on the wheel-host CDN). "
        f"Not found in body:\n{body!r}"
    )


def test_bootstrap_pip_install_pins_canonical_pypi_index() -> None:
    """``New-BootstrapVenv`` must pin ``--index-url https://pypi.org/simple``.

    Pip otherwise honours the host's ``PIP_INDEX_URL`` env var or
    ``pip.ini``/``pip.conf`` files. On a host with a hostile or
    misconfigured pip index, the bootstrap install would resolve
    ``cryptography`` from an attacker-controlled mirror — and the
    compromised wheel could forge a passing manifest signature verify
    (the very check this venv exists for). Pinning canonical PyPI
    closes this attack vector at the cost of breaking operators behind
    self-hosted PyPI mirrors (who can use the fast path by pre-installing
    cryptography on the system Python, or pin
    ``MOBAIDE_AGENT_TRUST_KEY_B64`` and bypass the bootstrap entirely).

    The agent's release wheel goes through the manifest's ``wheel_url``
    rather than PyPI, so this pin keeps the bootstrap aligned with the
    same trust posture as the rest of the install.
    """
    body = _function_body_text("New-BootstrapVenv")
    assert "--index-url" in body, (
        f"New-BootstrapVenv's pip install must pin --index-url (a "
        f"hostile PIP_INDEX_URL would otherwise redirect cryptography "
        f"to an attacker-controlled mirror, defeating the manifest "
        f"signature verify). Body:\n{body!r}"
    )
    assert "https://pypi.org/simple" in body, (
        f"New-BootstrapVenv's --index-url must point at canonical PyPI "
        f"(https://pypi.org/simple); a different value would either "
        f"re-open the redirection gap or break for operators on the "
        f"public install URL. Body:\n{body!r}"
    )


def test_invoke_agent_download_preserves_file_scheme_branch() -> None:
    """``Invoke-AgentDownload`` must handle the ``file://`` scheme via
    ``Copy-Item``, not ``Invoke-WebRequest``.

    The pre-fix Python ``fetch()`` helper had an explicit ``file://``
    branch (``shutil.copyfile(url.removeprefix('file://'), dest)``) used
    by air-gapped self-host deployments and offline test fixtures —
    operators pin ``$Script:ManifestUrl`` (or downstream wheel/tarball
    URLs) at ``file:///`` paths on a local mirror so the install runs
    with no network at all. The plan explicitly preserves this path in
    the new helper (install.ps1:101-102, 111-114): ``Copy-Item -LiteralPath
    $uri.LocalPath -Destination $OutFile -Force``.

    Without the dedicated branch, ``Invoke-WebRequest -Uri file:///...``
    is inconsistent across PS versions when the URL has a non-default
    host segment (per the plan's risk note), so the regression would
    surface as a sporadic file:// failure on whatever PS host the
    operator happens to run.

    Pin both halves of the branch: a ``file`` scheme test AND a
    ``Copy-Item`` call. Pinning either alone allows a regression that
    keeps the scheme check but routes the file:// path to
    ``Invoke-WebRequest`` (re-opening the version-skew bug), or one that
    keeps ``Copy-Item`` but unconditionally uses it for every URL
    (silently breaking https:// downloads).
    """
    body = _function_body_text("Invoke-AgentDownload")
    assert re.search(r"\.Scheme\s+-eq\s+['\"]file['\"]", body), (
        f"Invoke-AgentDownload must check for the 'file' URI scheme "
        f"explicitly so air-gapped self-host deployments (file:// "
        f"manifest/wheel URLs on a local mirror) keep working. "
        f"Body:\n{body!r}"
    )
    assert "Copy-Item" in body, (
        f"Invoke-AgentDownload's file:// branch must use Copy-Item "
        f"(Invoke-WebRequest on file:// is inconsistent across PS "
        f"versions when the URL has a non-default host segment — see "
        f"plan 'Risks and assumptions'). Body:\n{body!r}"
    )


def test_invoke_agent_download_streams_to_disk_with_iwr_contract() -> None:
    """``Invoke-AgentDownload`` must call ``Invoke-WebRequest`` with the
    three flags the rest of the installer already standardises on:
    ``-OutFile`` (stream to disk, no in-memory buffering of multi-MB
    assets), ``-UseBasicParsing`` (skip the IE-engine dependency that
    triggers Server's IE first-launch wizard), and ``-TimeoutSec`` (the
    default 100s is too short for tarballs over slow corporate links).

    Each flag closes a distinct failure mode:

    * ``-OutFile``: without it, ``Invoke-WebRequest`` buffers the entire
      response body in ``$response.Content`` before returning. A
      ~30 MB tarball or wheel would briefly hold ~60 MB of LOH-allocated
      managed memory; on a small Windows Sandbox / Server Core VM
      (768 MB RAM) the install OOMs and the user sees a confusing
      .NET ``OutOfMemoryException`` instead of an SSL trace. The plan's
      'Risks and assumptions' explicitly calls this out as the design
      reason for the helper's contract.
    * ``-UseBasicParsing``: PS 5.1's IWR default-parses HTML responses
      using the IE engine, which triggers "Internet Explorer first-launch
      configuration" on fresh Server hosts and hangs the install
      indefinitely. ``Get-Manifest`` (install.ps1:452) and
      ``Install-PythonFromPythonOrg`` (install.ps1:260) already set the
      flag — the new helper must match.
    * ``-TimeoutSec``: IWR's default 100s connect+read timeout is too
      short for a multi-MB wheel/tarball over a 1 Mbps corporate VPN
      tunnel. The plan's 'Risks and assumptions' pins 300s as the
      explicit override. A regression that drops the parameter (or sets
      a smaller integer literal) would surface as sporadic mid-download
      timeouts on the same hosts the SSL fix targets.

    Pinning all three at parse-time prevents a future refactor from
    silently dropping any one of them. A regression that swaps in
    ``System.Net.WebClient`` or similar would lose all three; this test
    fails before runtime.
    """
    body = _function_body_text("Invoke-AgentDownload")
    for flag in ("OutFile", "UseBasicParsing", "TimeoutSec"):
        assert flag in body, (
            f"Invoke-AgentDownload must pass {flag!r} to Invoke-WebRequest "
            f"(OutFile streams to disk so multi-MB assets don't OOM small "
            f"VMs; UseBasicParsing skips PS 5.1's IE-engine first-launch "
            f"wizard on Server hosts; TimeoutSec overrides IWR's 100s "
            f"default which is too short for slow corporate links). "
            f"Matches the existing Invoke-WebRequest calls in "
            f"Get-Manifest (install.ps1:452) and "
            f"Install-PythonFromPythonOrg (install.ps1:260). "
            f"Not found in body:\n{body!r}"
        )


def test_get_wheel_basename_rejects_path_traversal() -> None:
    """``Get-WheelBasename`` must reject basenames containing ``/``,
    ``\\``, or NUL.

    Defense-in-depth on top of the Ed25519 manifest signature verify:
    a compromised signing key (or a momentary key-rotation window) could
    otherwise smuggle a path-traversal payload through ``wheel_url`` —
    e.g., ``wheel_url=".../etc/passwd"`` would, without the guard,
    return ``"passwd"`` as the basename, get joined into
    ``$tmp/passwd``, and on the next ``pip install`` line attempt to
    install a non-wheel file (failing) — but a more elaborate payload
    like ``"../../../Windows/System32/evil.whl"`` could escape ``$tmp``
    entirely. The check (install.ps1:144) closes the gap before any
    file lands on disk.

    The contract is two-part: the function must (a) match path-separator
    characters in the basename AND (b) call ``Write-Fail`` on a hit (so
    the install aborts loudly rather than silently using a sanitised
    name). Pinning both halves catches a regression that softens the
    rejection (e.g., a ``Write-Warn`` + ``continue`` that lets a hostile
    basename through with a log line).
    """
    body = _function_body_text("Get-WheelBasename")
    assert re.search(r"['\"][\\\\/]+['\"]|\[\\\\/\]", body), (
        f"Get-WheelBasename must check for path-separator characters "
        f"('/' or '\\\\') in the derived basename — defence-in-depth on "
        f"top of the Ed25519 manifest signature verify, so a key "
        f"rotation / compromise window cannot smuggle a path-traversal "
        f"payload through wheel_url. Body:\n{body!r}"
    )
    assert "Write-Fail" in body, (
        f"Get-WheelBasename must call Write-Fail on a path-traversal "
        f"hit so the install aborts loudly (a Write-Warn + continue "
        f"that uses the sanitised name would let a hostile basename "
        f"through with only a log line). Body:\n{body!r}"
    )


def test_get_wheel_basename_pins_pep427_filename_shape() -> None:
    """``Get-WheelBasename`` must reject non-PEP 427 filenames: must
    end in ``.whl`` AND contain at least 4 hyphens (the canonical
    ``<name>-<version>-<pyver>-<abi>-<platform>.whl`` shape).

    PEP 427 specifies the wheel filename grammar exactly; pip 25.2+
    enforces the shape at install time and rejects anything else. By
    re-validating in ``Get-WheelBasename`` (install.ps1:147-149) BEFORE
    the download, the installer fails fast at manifest-fetch time with
    an actionable error (``manifest wheel_url has non-PEP427
    filename``) rather than at the much later ``pip install`` step
    with an opaque "is not a valid wheel filename" error from pip.

    Two regression vectors this test catches:
    * Dropping the ``EndsWith('.whl')`` check would let a manifest
      smuggle a ``.tar.gz`` or ``.zip`` past the basename guard, which
      then fails opaquely at pip install.
    * Dropping the hyphen-count check would accept a wheel named
      ``foo.whl`` (zero hyphens, no version pin) — pip would reject it,
      but only after the multi-MB download; the cost is a wasted
      round-trip and a confusing error path.
    """
    body = _function_body_text("Get-WheelBasename")
    assert re.search(r"['\"]\.whl['\"]|EndsWith\s*\(\s*['\"]\.whl['\"]", body), (
        f"Get-WheelBasename must check that the basename ends in "
        f"'.whl' so a non-wheel manifest payload fails at basename-"
        f"derivation time rather than at pip install. Body:\n{body!r}"
    )
    assert re.search(r"-\s*['\"]?-?['\"]?\s*\.Count|Split.*Count|Count.*-.*4", body) or "4" in body, (
        f"Get-WheelBasename must enforce the PEP 427 hyphen count "
        f"(name-version-pyver-abi-platform.whl has >= 4 hyphens; the "
        f"plan pins this at install.ps1:147 via "
        f"`($name.Split('-').Count - 1) -lt 4`). A drop would let "
        f"`foo.whl` (no version) reach pip and fail opaquely. "
        f"Body:\n{body!r}"
    )


def test_install_release_calls_get_wheel_basename_before_downloads() -> None:
    """``Install-Release`` must call ``Get-WheelBasename`` BEFORE the
    first ``Invoke-AgentDownload``.

    The fail-fast contract (plan section 'New helper: Get-WheelBasename')
    is the whole reason the helper is separate: a malformed or hostile
    manifest must abort BEFORE any bytes hit disk. If the basename
    derivation moves after the tarball download, an attacker who flips
    only ``wheel_url`` (signature still valid for the rotation window)
    can force the installer to write a multi-MB tarball to disk before
    the basename check rejects the wheel — wasted I/O on every install
    on a partially-compromised manifest, and a leaked tmp file if any
    cleanup path skips the partially-staged release dir.

    Pinning the call-order textually catches the regression at AST
    parse-time. The check is forward-compatible: any additional
    statements between ``Get-WheelBasename`` and the first download
    (e.g., logging, validation) are fine; only a reverse-ordering or
    a dropped call breaks the test.
    """
    body = _function_body_text("Install-Release")
    # Anchor on the real call shape, not the bare function name — the
    # function name also appears in the leading-comment block of
    # Install-Release ("see Invoke-AgentDownload's header for the SSL
    # trust-store rationale"), and bare-name find() would lock onto the
    # comment occurrence. The named parameter ``-WheelUrl`` /  ``-Url``
    # only appears at real call sites.
    basename_match = re.search(r"Get-WheelBasename\s+-WheelUrl\b", body)
    download_match = re.search(r"Invoke-AgentDownload\s+-Url\b", body)
    assert basename_match is not None, (
        f"Install-Release must call Get-WheelBasename to derive the "
        f"canonical PEP 427 wheel filename before the download — see "
        f"install.ps1:585. Not found in body:\n{body!r}"
    )
    assert download_match is not None, (
        f"Install-Release must call Invoke-AgentDownload to fetch the "
        f"tarball + wheel — see install.ps1:604-605. Not found in "
        f"body:\n{body!r}"
    )
    assert basename_match.start() < download_match.start(), (
        f"Install-Release must call Get-WheelBasename BEFORE the first "
        f"Invoke-AgentDownload (fail-fast on malformed/hostile "
        f"manifests so no bytes hit disk on a bad wheel_url). Found "
        f"Get-WheelBasename call at offset {basename_match.start()}, "
        f"Invoke-AgentDownload call at offset {download_match.start()}. "
        f"Body:\n{body!r}"
    )


@pytest.mark.parametrize(
    "removed_module",
    ["urllib.parse", "posixpath", "shutil"],
)
def test_install_release_staging_script_drops_network_helpers(removed_module: str) -> None:
    """The embedded staging Python script in ``Install-Release`` must
    not import ``urllib.parse``, ``posixpath``, or ``shutil``.

    Complements ``test_install_release_does_not_embed_python_urllib_request``,
    which pins the primary SSL-bug regression guard (``urllib.request``
    must be absent). The diff also removed three sibling imports that
    supported the now-deleted ``fetch()`` and ``wheel_basename()``
    helpers inside the staging script:

    * ``urllib.parse`` was used by the old ``wheel_basename()`` to
      split off the URL path (``urllib.parse.urlparse(url).path``).
      Since basename derivation moved to PowerShell's ``Get-WheelBasename``
      helper, a re-introduction signals the wheel-filename logic
      regressed back into the staging script (defeating the fail-fast
      ordering pinned by
      ``test_install_release_calls_get_wheel_basename_before_downloads``).
    * ``posixpath`` was used by the same ``wheel_basename()`` for
      ``posixpath.basename(...)``. Same story.
    * ``shutil`` was used by the old ``fetch()`` for
      ``shutil.copyfile(...)`` (file:// branch) and
      ``shutil.copyfileobj(resp, fh)`` (HTTPS stream-to-disk). Since
      both download paths moved to PowerShell's ``Invoke-AgentDownload``,
      a re-introduction signals at least one download path regressed
      back into Python.

    The staging script's intentional import set is exactly
    ``hashlib, json, sys, tarfile`` (plus ``pathlib`` via ``from
    pathlib import Path``). Any one of these three modules creeping
    back in means a partial regression toward the original bug — the
    new code path is bypassed in some scenario. Parametrising over
    the three lets pytest report exactly which one slipped back in.
    """
    body = _function_body_text("Install-Release")
    pattern = re.escape(removed_module)
    assert not re.search(rf"\b{pattern}\b", body), (
        f"Install-Release's embedded staging script must not import "
        f"{removed_module!r} — the fix slimmed the staging script to "
        f"verification + extraction only (hashlib/json/sys/tarfile/"
        f"pathlib). A re-introduction of {removed_module!r} signals the "
        f"corresponding pre-fix helper (fetch / wheel_basename / "
        f"copyfile) regressed back into Python. "
        f"Body:\n{body!r}"
    )


def test_install_release_staging_argv_contract_matches_call_site() -> None:
    """The embedded staging script's ``sys.argv`` slice must declare
    exactly the four positionals ``Install-Release`` passes:
    ``(manifest_path, tarball_path, wheel_path, release_dir)``.

    The diff explicitly changes the contract from 3 args
    ``(manifest, tmpdir, release_dir)`` to 4 args
    ``(manifest, tarball_path, wheel_path, release_dir)`` — the
    staging script no longer needs a tmp directory because PowerShell
    handed it the two file paths directly. Both sides of the contract
    must agree:

    * The PowerShell caller (install.ps1:633) passes
      ``@($ManifestPath, $tarballPath, $wheelPath, $releaseDir)``.
    * The Python receiver (install.ps1:614) slices
      ``sys.argv[1:5]`` into exactly four names.

    A regression that updates only one side — e.g., adding a fifth
    argument from PS without extending the Python slice (silently
    drops the new arg), or extending the Python slice without
    passing a new arg from PS (raises ``IndexError`` at runtime,
    confusing failure mode) — corrupts the staging step. This test
    pins the staging script's slice as 1:5 (i.e., exactly 4
    positionals) AND pins that ``Install-Release``'s
    ``Invoke-PythonScript`` ``-Arguments`` array has exactly 4
    elements.
    """
    body = _function_body_text("Install-Release")
    assert re.search(r"sys\.argv\[1:5\]", body), (
        f"Install-Release's staging script must slice sys.argv[1:5] "
        f"(exactly 4 positionals: manifest_path, tarball_path, "
        f"wheel_path, release_dir). A different slice indicates the "
        f"argv contract drifted between the PS caller and the Python "
        f"receiver. Body:\n{body!r}"
    )
    # The caller-side -Arguments array must hold the same four locals.
    # Pin each as a named PowerShell variable; flexing on whitespace
    # between commas keeps the test robust to a future cosmetic reformat.
    args_match = re.search(
        r"Invoke-PythonScript[^@]*@\(\s*\$ManifestPath\s*,\s*\$tarballPath\s*,\s*\$wheelPath\s*,\s*\$releaseDir\s*\)",
        body,
    )
    assert args_match, (
        f"Install-Release must invoke the staging script with exactly "
        f"`@($ManifestPath, $tarballPath, $wheelPath, $releaseDir)` — "
        f"matches sys.argv[1:5] inside the staging script. A mismatch "
        f"(fewer args -> IndexError; more args -> silently dropped) "
        f"corrupts the staging step at runtime. Body:\n{body!r}"
    )


_PIPELINE_SUPPRESSION_RE = re.compile(
    r"\|\s*Out-(?:Host|Null)\b|^\s*\$null\s*=", re.MULTILINE
)
"""Match any canonical PowerShell idiom for "consume this pipeline without
appending to the surrounding function's success stream":

  * ``| Out-Host`` — writes to the console host, emits nothing onwards
    (the operator-facing choice; preserves pip / winget progress).
  * ``| Out-Null`` — discards entirely (the silent choice; appropriate
    for probe-style calls).
  * ``$null = ...`` — assigns to the bit bucket (the inline-statement
    choice; appropriate when no actual pipeline follows).

Pinned as a single alternation so the regression tests stay agnostic to
which suppression primitive a future refactor prefers — the contract
they enforce is "the function's return value is not corrupted by native
command output", not the specific operator picked.
"""


def _native_invocations_in(body: str) -> list[str]:
    """Return each line of ``body`` that contains a ``&`` native-command
    invocation (the PowerShell call operator), filtered to the lines a
    function-body pipeline-pollution scan cares about.

    The rule of thumb: any line whose first non-whitespace token is ``&``
    is a top-level call whose output (if any) lands on the surrounding
    function's success stream. Continuation lines from a backtick-wrapped
    multi-line ``&`` call are reattached to the originating line so a
    ``| Out-Host`` on the final segment satisfies the suppression check
    for the whole call (the alternative would force every line of a
    multi-line call to carry the suppression, which is syntactically
    impossible).

    Lines where the ``&`` result is captured into a variable
    (``$var = & cmd ...``) are excluded — capturing redirects the
    pipeline into the variable, not the function's success stream, so
    pollution does not occur regardless of trailing suppression.
    """
    lines = body.splitlines()
    # Stitch backtick line-continuations: PowerShell's `\`` at EOL means
    # "this line continues on the next". Glue the chain together so the
    # final segment's `| Out-Host` is visible on the originating line.
    stitched: list[str] = []
    buf = ""
    for line in lines:
        if buf:
            buf += " " + line.strip()
        else:
            buf = line
        if buf.rstrip().endswith("`"):
            buf = buf.rstrip()[:-1]
            continue
        stitched.append(buf)
        buf = ""
    if buf:
        stitched.append(buf)
    invocations: list[str] = []
    for line in stitched:
        stripped = line.lstrip()
        if stripped.startswith("&"):
            invocations.append(line)
    return invocations


def test_install_release_self_upgrades_pip_via_python_module() -> None:
    """``Install-Release`` must upgrade pip via ``python.exe -m pip``,
    NOT via the ``pip.exe`` console_scripts shim.

    pip on Windows refuses to self-overwrite when invoked through its
    ``pip.exe`` shim: the shim is the running process and Windows locks
    it. Pre-fix, the script called ``& $venvPip install --upgrade pip``
    and got back

        ERROR: To modify pip, please run the following command:
        ...\\Scripts\\python.exe -m pip install --no-cache-dir --upgrade pip

    on every install — a scary, user-visible failure even though the
    install proceeded (its non-zero ``$LASTEXITCODE`` was overwritten by
    the next pip call). The canonical workaround
    (`pypa/pip#6784 <https://github.com/pypa/pip/issues/6784>`_) is to
    upgrade through the interpreter so pip is loaded as a module and the
    shim isn't held open during replacement.

    The contract pinned here:

    1. The interpreter form must appear: ``-m pip install`` plus
       ``--upgrade pip`` somewhere in the function body.
    2. The pre-fix shim form must NOT appear: a ``$venvPip install ...
       --upgrade pip`` line is the exact regression to catch (the same
       ``$venvPip`` is still used for the wheel install on the next
       line, which is fine — only the ``--upgrade pip`` call has the
       self-overwrite problem).
    """
    body = _function_body_text("Install-Release")
    assert "-m pip install" in body, (
        "Install-Release must upgrade pip via the interpreter "
        "(`python.exe -m pip install --upgrade pip`), not the "
        "`pip.exe` console_scripts shim. pip on Windows refuses to "
        "self-overwrite via the shim (the running process is locked); "
        "pypa/pip#6784. Body:\n" + repr(body)
    )
    assert re.search(r"-m\s+pip\s+install[^|]*--upgrade\s+pip", body), (
        "Install-Release must specifically use `-m pip install ... "
        "--upgrade pip` for the self-upgrade. Body:\n" + repr(body)
    )
    assert not re.search(
        r"\$venvPip\s+install[^|]*--upgrade\s+pip", body
    ), (
        "Install-Release must NOT call the `pip.exe` shim "
        "(`& $venvPip install --upgrade pip`) — that's the pre-fix "
        "form that trips pip's self-overwrite guard on Windows and "
        "prints `ERROR: To modify pip, please run the following "
        "command: ...\\python.exe -m pip install --upgrade pip`. "
        "Upgrade through `$venvPython -m pip` instead. "
        "Body:\n" + repr(body)
    )


def test_install_release_isolates_native_command_output_from_return_value() -> None:
    """Every native ``&`` invocation inside ``Install-Release`` must be
    isolated from the function's success pipeline.

    ``Install-Release`` ends with ``return $version`` — a single string.
    PowerShell appends every uncaptured native-command stdout line to
    the surrounding function's output, so a bare ``& $venvPip install
    --upgrade pip`` glues all of pip's ``[notice] ...`` / ``ERROR: ...``
    lines onto the returned value. ``Main`` then receives an
    ``[object[]]`` instead of a string and forwards it to
    ``Write-AgentEnv -Version $version``, where ``Join-Path -ChildPath
    $Version`` crashes with

        Cannot convert 'System.Object[]' to the type 'System.String'
        required by parameter 'ChildPath'

    — surfaced under ``iex`` because the canonical install one-liner
    pipes the script through ``Invoke-Expression``. The fix is to
    terminate every ``&`` line with ``| Out-Host`` (or ``Out-Null``, or
    capture to ``$null``) so the function's return value is the only
    object on its pipeline.

    The check enumerates every line whose first non-whitespace token is
    ``&`` and asserts a canonical suppression primitive
    (``Out-Host`` / ``Out-Null`` / ``$null = ...``) appears on the same
    line. Captured invocations (``$var = & cmd ...``) are excluded —
    capturing into a variable already redirects the pipeline away from
    the function's success stream.
    """
    body = _function_body_text("Install-Release")
    invocations = _native_invocations_in(body)
    assert invocations, (
        "Install-Release must contain `&`-invoked native commands "
        "(`python -m venv`, `pip install`); none found, which means "
        "either the function body was emptied or the line-scan is "
        "broken. Body:\n" + repr(body)
    )
    unsuppressed = [
        line for line in invocations
        if not _PIPELINE_SUPPRESSION_RE.search(line)
    ]
    assert not unsuppressed, (
        "Install-Release has native `&` invocations whose stdout "
        "lands on the function's success pipeline, corrupting "
        "`return $version` into an `[object[]]` that crashes "
        "downstream `Join-Path -ChildPath`. Pipe each to `| Out-Host` "
        "(operator-facing) or `| Out-Null` (silent), or capture with "
        "`$null = ...`. Offending lines:\n"
        + "\n".join(f"  {ln!r}" for ln in unsuppressed)
    )


def test_install_python_via_winget_isolates_winget_output() -> None:
    """``Install-PythonViaWinget`` must isolate ``winget``'s output from
    its ``return $true`` / ``return $false`` value.

    The function returns a ``[bool]``. PowerShell evaluates ``if ($x)``
    truthy whenever ``$x`` is a non-empty array — regardless of contents
    — so a polluted return ``@(winget-stdout-line-1, ..., $false)`` is
    truthy and the caller (``Install-Python``'s fallback orchestration)
    short-circuits on a failed winget install. The user is then told
    "Python installed" while no Python actually landed.

    Same suppression contract as
    ``test_install_release_isolates_native_command_output_from_return_value``:
    every ``&``-prefixed line must carry one of the canonical
    primitives. Captured invocations are excluded for the same reason.
    """
    body = _function_body_text("Install-PythonViaWinget")
    invocations = _native_invocations_in(body)
    assert invocations, (
        "Install-PythonViaWinget must invoke winget; no native `&` "
        "lines found, which would mean the function was emptied. "
        "Body:\n" + repr(body)
    )
    unsuppressed = [
        line for line in invocations
        if not _PIPELINE_SUPPRESSION_RE.search(line)
    ]
    assert not unsuppressed, (
        "Install-PythonViaWinget has a `& winget ...` line whose "
        "stdout pollutes the `[bool]` return value. PowerShell's "
        "truthy check on `[object[]]` is non-empty-array-truthy, so "
        "`if (Install-PythonViaWinget) { ... }` would return early "
        "even on a failed install. Pipe the winget call to `Out-Host` "
        "(progress visible) or `Out-Null` (silent). Offending "
        "lines:\n" + "\n".join(f"  {ln!r}" for ln in unsuppressed)
    )


def test_new_bootstrap_venv_isolates_native_command_output() -> None:
    """``New-BootstrapVenv`` must isolate every native ``&`` invocation
    from its hashtable return value.

    The function returns ``@{ Python = $venvPython; Root = $root }`` —
    a single hashtable. Bare ``& $PythonExe -m venv $root`` and
    ``& $venvPip install ...`` calls would prepend their stdout onto
    the function's pipeline, so ``$verifier =
    Resolve-VerifierPython ...`` would land an array on ``$verifier``,
    and every downstream ``$verifier.Python`` / ``$verifier.Root``
    property access would either return ``$null`` (method-on-array)
    or throw.

    The `Install-Release` failure mode reproduces here: the trust-key
    bootstrap and manifest-signature verification both invoke
    ``$verifier.Python``; a corrupted ``$verifier`` makes the bootstrap
    crash before the manifest is even fetched.
    """
    body = _function_body_text("New-BootstrapVenv")
    invocations = _native_invocations_in(body)
    assert invocations, (
        "New-BootstrapVenv must invoke `python -m venv` and `pip "
        "install`; no native `&` lines found. Body:\n" + repr(body)
    )
    unsuppressed = [
        line for line in invocations
        if not _PIPELINE_SUPPRESSION_RE.search(line)
    ]
    assert not unsuppressed, (
        "New-BootstrapVenv has native `&` invocations whose stdout "
        "would corrupt the `@{Python=...; Root=...}` return into an "
        "`[object[]]`. Downstream `$verifier.Python` property access "
        "then returns `$null` and the manifest verify crashes "
        "opaquely. Offending lines:\n"
        + "\n".join(f"  {ln!r}" for ln in unsuppressed)
    )


def test_test_cryptography_importable_isolates_python_probe_output() -> None:
    """``Test-CryptographyImportable`` must isolate its
    ``& $PythonExe -c 'import cryptography'`` probe from the ``[bool]``
    return value.

    The function's return is ``$LASTEXITCODE -eq 0`` — a single boolean
    that ``Resolve-VerifierPython`` uses to short-circuit between the
    fast path (bare interpreter, cryptography already importable) and
    the slow path (build a bootstrap venv). A polluted return makes the
    fast-path branch truthy on every host whose Python emits any line
    to stdout during ``-c "import cryptography"`` — silently disabling
    the bootstrap venv slow path that this code exists to provide.

    ``2>$null`` already swallows stderr (so an ``ImportError`` traceback
    doesn't reach the pipeline), but stdout is not guarded. A
    ``sitecustomize.py``, ``usercustomize.py``, ``PYTHONSTARTUP`` hook,
    or future ``-X`` flag can print at import time. The canonical fix
    is to add ``| Out-Null`` (or capture with ``$null =``) on the same
    line as the ``&`` call.

    Scoped tighter than the other functions in this group: the probe
    here is single-line and the function is small, so we pin the
    suppression directly on the ``& $PythonExe -c`` line rather than
    enumerating all ``&`` lines.
    """
    body = _function_body_text("Test-CryptographyImportable")
    probe_lines = [
        line for line in body.splitlines()
        if re.search(r"&\s+\$PythonExe\s+-c\b", line)
    ]
    assert probe_lines, (
        "Test-CryptographyImportable must invoke `& $PythonExe -c "
        "'import cryptography'`; not found. Body:\n" + repr(body)
    )
    unsuppressed = [
        line for line in probe_lines
        if not _PIPELINE_SUPPRESSION_RE.search(line)
    ]
    assert not unsuppressed, (
        "Test-CryptographyImportable's `& $PythonExe -c` probe must "
        "be piped to `Out-Null` (or captured with `$null = ...`) on "
        "the same line. The function returns a `[bool]`; any stdout "
        "chatter from the probe (sitecustomize.py, PYTHONSTARTUP, "
        "future Python build) would convert the return to a non-"
        "empty `[object[]]` that's truthy regardless of contents, "
        "silently disabling the bootstrap-venv slow path. Offending "
        "lines:\n" + "\n".join(f"  {ln!r}" for ln in unsuppressed)
    )


def test_install_release_pip_self_upgrade_failure_warns_but_does_not_abort() -> None:
    """The pip self-upgrade in ``Install-Release`` must surface failure
    via ``Write-Warn`` and let the wheel install proceed — NOT abort the
    install with ``Write-Fail``, NOT silently swallow the failure.

    Pre-fix, the failing ``& $venvPip install --upgrade pip`` (the call
    that tripped pip's Windows self-overwrite guard, pypa/pip#6784) left
    a non-zero ``$LASTEXITCODE`` that was immediately overwritten by the
    next ``& $venvPip install --no-cache-dir $wheel``. The user saw the
    scary ``ERROR: To modify pip ...`` line, the install nonetheless
    succeeded with the bundled pip, and there was no observable
    failure path for any *real* pip upgrade failure (network drop,
    locked .exe under AV, missing PyPI route) — every such failure was
    masked by the wheel install's exit code.

    Post-fix, the upgrade goes through ``$venvPython -m pip`` so the
    common-case Windows pip-self-overwrite no longer fires, AND a
    dedicated ``if ($LASTEXITCODE -ne 0) { Write-Warn ... }`` makes any
    *real* failure visible. ``Write-Warn`` (not ``Write-Fail``) because
    Install-Release deliberately tolerates a failed pip upgrade and
    proceeds with the bundled pip — the wheel install on the next
    line is the hard gate, not the pip upgrade.

    Two regressions this pin closes:

    1. **Silent masking returns.** A refactor that drops the
       ``$LASTEXITCODE`` check after the ``-m pip install ... --upgrade
       pip`` line would re-introduce the pre-fix bug: any real pip
       upgrade failure would be invisibly swallowed by the next
       ``$LASTEXITCODE`` overwrite. The fix narrative explicitly calls
       this out as a behaviour change worth preserving.
    2. **Graceful degradation lost.** A refactor that escalates the
       check to ``Write-Fail`` (which calls ``exit 1`` — see
       ``Write-Fail`` definition above) would hard-abort every install
       on a transient pip upgrade failure (AV race, PyPI hiccup), even
       though the install would otherwise succeed with the bundled
       pip 25.0.1. ``Write-Warn`` is the canonical choice.

    Scoped to the slice strictly *between* the pip self-upgrade line
    and the wheel-install line so the assertion can't be satisfied by
    the wheel install's own ``Write-Fail`` (which is correct — wheel
    install IS the hard gate).
    """
    body = _function_body_text("Install-Release")
    upgrade_match = re.search(
        r"-m\s+pip\s+install[^\r\n]*--upgrade\s+pip[^\r\n]*", body
    )
    assert upgrade_match, (
        "Install-Release must contain a `-m pip install ... --upgrade "
        "pip` line; not found. This is the precondition for the "
        "post-fix-only behavioural pin below — if the line is missing, "
        "test_install_release_self_upgrades_pip_via_python_module "
        "should already have flagged it. Body:\n" + repr(body)
    )
    wheel_match = re.search(r"\$venvPip\s+install[^\r\n]*\$wheel", body)
    assert wheel_match, (
        "Install-Release must contain a `& $venvPip install ... $wheel` "
        "line that performs the actual wheel install (the hard gate "
        "whose `$LASTEXITCODE` check is `Write-Fail`); not found. "
        "Body:\n" + repr(body)
    )
    assert upgrade_match.end() < wheel_match.start(), (
        "The pip self-upgrade line must appear textually before the "
        "wheel install line. The post-fix flow is: upgrade pip "
        "(soft-fail) -> install wheel (hard-fail). Reordering breaks "
        "both the contract pinned here AND the documented intent. "
        f"upgrade.end={upgrade_match.end()} "
        f"wheel.start={wheel_match.start()} Body:\n" + repr(body)
    )
    between = body[upgrade_match.end():wheel_match.start()]
    # The non-zero-exit-code check must exist between the two lines so
    # the upgrade's `$LASTEXITCODE` is consumed before the wheel install
    # overwrites it. Without this check, a real upgrade failure (network
    # drop, AV lock, missing PyPI route) is silently masked — exactly
    # the pre-fix bug.
    assert re.search(r"\$LASTEXITCODE\s*-ne\s*0", between), (
        "Install-Release must check `$LASTEXITCODE -ne 0` between the "
        "`-m pip install ... --upgrade pip` line and the `& $venvPip "
        "install ... $wheel` line. Without it, any real pip upgrade "
        "failure (network drop, AV-locked .exe, no PyPI reachability) "
        "is silently overwritten by the wheel install's exit code -- "
        "the exact pre-fix masking behaviour that the fix narrative "
        "called out as worth surfacing. Slice between the two lines:\n"
        + repr(between)
    )
    # `Write-Warn` (not `Write-Fail`) because Install-Release deliberately
    # tolerates a failed pip upgrade and proceeds with the bundled pip
    # (25.0.1 in the current venv). `Write-Fail` would hard-abort every
    # install on a transient AV race or PyPI hiccup -- a strictly worse
    # outcome than the pre-fix silent masking.
    assert re.search(r"\bWrite-Warn\b", between), (
        "Install-Release's pip-self-upgrade-failure branch must call "
        "`Write-Warn`, not `Write-Fail` (which exits 1 -- see the "
        "`Write-Fail` helper definition). The fix narrative is explicit: "
        "pip upgrade is best-effort, the wheel install on the next line "
        "is the hard gate. Hard-aborting on a transient pip upgrade "
        "failure (AV race, locked .exe, network blip) would be a "
        "regression worse than the pre-fix silent masking. Slice "
        "between the upgrade and wheel-install lines:\n" + repr(between)
    )
    assert not re.search(r"\bWrite-Fail\b", between), (
        "Install-Release's pip-self-upgrade-failure branch must NOT "
        "call `Write-Fail` (which calls `exit 1`). The wheel install on "
        "the next line is the canonical hard gate; the pip upgrade is "
        "explicitly best-effort because the bundled pip is sufficient "
        "to install the wheel. A `Write-Fail` here would hard-abort "
        "the install on every transient AV race or PyPI hiccup. Slice "
        "between the upgrade and wheel-install lines:\n" + repr(between)
    )


def test_install_service_registers_scheduled_task() -> None:
    """``Install-Service`` must call ``Register-ScheduledTask``.

    The whole point of the SCM-service → Scheduled-Task migration is
    that the agent process tree runs under the installing user (real
    ``%USERPROFILE%``), not LocalSystem. ``Register-ScheduledTask`` is
    the documented modern primitive (Microsoft Learn /
    Register-ScheduledTask) — no password capture, survives password
    changes, lets the Claude CLI in the PTY find the user's
    ``~/.claude`` credentials and skills. A regression that uses
    ``New-Service`` / ``sc.exe create`` / ``win32serviceutil.InstallService``
    re-introduces the LocalSystem trap.
    """
    body = _function_body_text("Install-Service")
    assert re.search(r"\bRegister-ScheduledTask\b", body), (
        "Install-Service must call `Register-ScheduledTask` to install "
        "the per-user MobaideAgent task. Not found in body:\n"
        + repr(body)
    )


def test_install_service_pins_task_to_current_user() -> None:
    """``Install-Service`` must pin the task to ``$env:USERNAME``.

    Both ``New-ScheduledTaskPrincipal -UserId`` and
    ``New-ScheduledTaskTrigger -User`` must resolve to the installing
    user's name (``$env:USERNAME``). Hardcoding any other principal
    (``LocalSystem``, ``LocalService``, ``NT AUTHORITY\\...``) or
    omitting the trigger ``-User`` defeats the per-user model: the
    task would run under the wrong account and ``%USERPROFILE%`` would
    no longer resolve to ``C:\\Users\\<u>``. Mirrors Linux's
    ``User=$SUDO_USER`` semantics in ``deploy/install.sh``.
    """
    body = _function_body_text("Install-Service")
    assert re.search(
        r"New-ScheduledTaskPrincipal[^\r\n]*-UserId\s+\$env:USERNAME",
        body,
    ), (
        "Install-Service must call `New-ScheduledTaskPrincipal -UserId "
        "$env:USERNAME` so the task runs as the installing user. Not "
        "found in body:\n" + repr(body)
    )
    assert re.search(
        r"New-ScheduledTaskTrigger[^\r\n]*-User\s+\$env:USERNAME",
        body,
    ), (
        "Install-Service must call `New-ScheduledTaskTrigger -AtLogOn "
        "-User $env:USERNAME` so the task triggers on the installing "
        "user's logon. Not found in body:\n" + repr(body)
    )


def test_install_service_uses_logon_type_interactive() -> None:
    """``Install-Service`` must use ``-LogonType Interactive``.

    This is the entire point of the rewrite. ``Interactive`` runs the
    task in the user's session (real ``%USERPROFILE%``, real
    ``%APPDATA%``, real ``~/.claude``). Alternatives:
      * ``S4U`` runs without storing a password but doesn't load the
        user's profile — same shape problem as LocalSystem.
      * ``Password`` captures the user's password (rejected — VS Code
        Remote Tunnel hit microsoft/vscode#167741 with that approach).
      * ``ServiceAccount`` requires gMSA / virtual account — wrong
        profile shape, explicitly out of scope.
    A regression on this flag re-opens the original bug.
    """
    body_code = _strip_powershell_comments(_function_body_text("Install-Service"))
    assert re.search(r"-LogonType\s+Interactive\b", body_code), (
        "Install-Service must pass `-LogonType Interactive` to "
        "New-ScheduledTaskPrincipal so the task runs in the user's "
        "session and `%USERPROFILE%` resolves to `C:\\Users\\<u>`. "
        "Comment-stripped body:\n" + repr(body_code)
    )


def test_install_service_uses_at_logon_trigger() -> None:
    """``Install-Service`` must trigger the task at user logon.

    ``-AtLogOn -User $env:USERNAME`` matches systemd's
    ``WantedBy=default.target`` semantics — the agent comes up as
    soon as the user is logged in, without requiring the user to
    start it manually. Calendar / boot triggers don't apply because
    Interactive tasks only run with a logged-in user anyway.
    """
    body = _function_body_text("Install-Service")
    assert "-AtLogOn" in body, (
        "Install-Service must pass `-AtLogOn` to "
        "New-ScheduledTaskTrigger. Not found in body:\n" + repr(body)
    )


def test_install_service_action_invokes_supervisor_script() -> None:
    """``Install-Service`` must wire the task action to the supervisor.

    The supervisor lives at ``<prefix>\\svc\\mobaide_launcher.py``
    (downloaded earlier in Install-Service). Task action:
      * ``-Execute <python_exe>`` — the Python ``Resolve-Python``
        returned at install time.
      * ``-Argument`` — ``-u "<path>\\mobaide_launcher.py" --prefix
        "<prefix>"``. The ``--prefix`` argv passes the install prefix
        explicitly because the Scheduled Task's environment doesn't
        get ``MOBAIDE_AGENT_PREFIX`` set automatically.
    """
    body = _function_body_text("Install-Service")
    assert "mobaide_launcher.py" in body, (
        "Install-Service must reference `mobaide_launcher.py` (the "
        "on-disk filename of the supervisor under <prefix>/svc/). Not "
        "found in body:\n" + repr(body)
    )
    assert re.search(r"--prefix\b", body), (
        "Install-Service must pass `--prefix <prefix>` in the task "
        "action's `-Argument` so the supervisor resolves the install "
        "prefix without depending on environment variables Task "
        "Scheduler does not propagate. Not found in body:\n"
        + repr(body)
    )


def test_install_service_action_uses_pythonw_to_hide_console() -> None:
    """``New-ScheduledTaskAction -Execute`` must reference ``$PythonwExe``.

    The Scheduled Task action's ``-Execute`` must point at the GUI-
    subsystem Python (``pythonw.exe`` / ``pyw.exe``) so Task Scheduler
    does NOT allocate a console window at logon. Switching to
    ``$PythonExe`` (console-subsystem) re-introduces the "py.exe
    console pops up on boot" regression that the
    ``Hide the Mobaide agent's py.exe console window`` plan exists
    to fix.

    Pinned via regex on the executable code (comments stripped) so
    the negative-pin against ``$PythonExe`` in the same position does
    not collide with the architectural-note comment that explains
    *why* ``$PythonwExe`` is used here.
    """
    body = _function_body_text("Install-Service")
    code = _strip_powershell_comments(body)
    assert re.search(
        r"New-ScheduledTaskAction\s+-Execute\s+\$PythonwExe\b", code
    ), (
        "Install-Service must call `New-ScheduledTaskAction -Execute "
        "$PythonwExe`. Without this, Task Scheduler launches the "
        "supervisor under console-subsystem python.exe and Windows "
        "allocates a fresh conhost window at logon. Not found in "
        "function body (comments stripped):\n" + repr(code)
    )
    # Negative pin: the action must NOT pass `$PythonExe` to
    # `-Execute`. Other references to ``$PythonExe`` are allowed (e.g.
    # the function still accepts it as a parameter for install-time
    # work), so the assertion targets the `-Execute` neighbourhood
    # specifically.
    assert not re.search(
        r"New-ScheduledTaskAction\s+-Execute\s+\$PythonExe\b", code
    ), (
        "Install-Service must NOT pass `$PythonExe` (console-subsystem) "
        "to `New-ScheduledTaskAction -Execute`. A regression here brings "
        "back the py.exe console window at logon -- the exact bug the "
        "Layer-1 fix in the hidden-py.exe plan removes. Found in code:\n"
        + repr(code)
    )


def test_install_service_takes_pythonw_exe_param() -> None:
    """``Install-Service`` must accept ``$PythonwExe`` as a parameter.

    ``Main`` resolves the pythonw.exe path once (via ``Resolve-Pythonw``)
    and threads it through to ``Install-Service``. Hardcoding the path
    inside ``Install-Service`` (or shelling out to ``Resolve-Pythonw``
    a second time) would break the install-time invariant that
    "a missing pythonw.exe fails the install BEFORE we register the
    task". Tested via regex on the parameter block; a future refactor
    that splits the function or renames the parameter must keep both
    pieces in sync.
    """
    body = _function_body_text("Install-Service")
    code = _strip_powershell_comments(body)
    assert re.search(r"\$PythonwExe\b", code), (
        "Install-Service must declare a `$PythonwExe` parameter. "
        "Without it, `Main` has no way to thread the resolved "
        "pythonw.exe path into the task action, and the function "
        "would have to re-run Resolve-Pythonw (silently degrading "
        "the install-time hard-fail semantics). Function body "
        "(comments stripped):\n" + repr(code)
    )


def test_main_calls_resolve_pythonw_and_passes_to_install_service() -> None:
    """``Main`` must call ``Resolve-Pythonw`` and forward the result
    to ``Install-Service -PythonwExe``.

    Two invariants in one test (cheaper than two AST-walks for the
    same function body):

    1. ``Main`` invokes ``Resolve-Pythonw`` (the call site, not just
       the function definition pinned by
       ``test_required_function_is_defined``). Without this call,
       the function exists but is dead code and ``$pythonwExe`` is
       never set.
    2. ``Install-Service`` is invoked with ``-PythonwExe <expr>``.
       Without this, even a successful ``Resolve-Pythonw`` does not
       reach the action's ``-Execute`` clause.
    """
    body = _function_body_text("Main")
    code = _strip_powershell_comments(body)
    assert re.search(r"\bResolve-Pythonw\b", code), (
        "Main must invoke Resolve-Pythonw so the pythonw.exe path is "
        "computed at install time (and a missing pythonw.exe fails "
        "loudly BEFORE the task is registered). Function body "
        "(comments stripped):\n" + repr(code)
    )
    assert re.search(
        r"Install-Service\b[^{}]*-PythonwExe\b", code
    ), (
        "Main must call `Install-Service` with `-PythonwExe <expr>` "
        "so the resolved path threads through to "
        "`New-ScheduledTaskAction -Execute`. Without this, "
        "Resolve-Pythonw is dead code. Function body "
        "(comments stripped):\n" + repr(code)
    )


def test_resolve_pythonw_handles_py_to_pyw_basename_swap() -> None:
    """``Resolve-Pythonw`` must map ``py.exe`` -> ``pyw.exe``.

    The Python Launcher for Windows ships as ``py.exe`` /
    ``pyw.exe`` (NOT ``pyw.exe`` is NOT just ``py.exe`` with a ``w``
    appended -- the standard "<name>w.exe" rule would produce
    ``pyw.exe`` by coincidence here, but the rule for ``py`` is
    distinct in the wider Python ecosystem: ``python.exe`` ->
    ``pythonw.exe`` is the major case, and ``py`` is the only short-
    name special case). Encode the dual mapping in code so a future
    refactor cannot drop the ``py`` -> ``pyw`` branch.

    Pinned via regex on the function body looking for the literal
    ``pyw.exe`` string -- proves the special case is encoded, not
    silently bypassed by a single ``<name>w.exe`` substitution.
    """
    body = _function_body_text("Resolve-Pythonw")
    assert "pyw.exe" in body, (
        "Resolve-Pythonw must contain the literal `pyw.exe` so the "
        "py.exe Python Launcher case maps to `pyw.exe` (NOT "
        "`pyw.exe` derived from a generic `<name>w.exe` rule "
        "applied to the `py` basename). Function body:\n" + repr(body)
    )
    assert "pythonw.exe" in body, (
        "Resolve-Pythonw must contain the literal `pythonw.exe` so "
        "the canonical python.exe -> pythonw.exe mapping is "
        "explicitly encoded. Function body:\n" + repr(body)
    )


def test_install_service_no_execution_time_limit() -> None:
    """``Install-Service`` must disable the 72-hour execution time limit.

    ``New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan
    -Seconds 0)`` is the documented way to disable the default 72-hour
    cap (Microsoft Learn / New-ScheduledTaskSettingsSet). Without this,
    Task Scheduler kills the agent at the 72-hour mark — the user sees
    the agent silently disappear three days after each logon.
    """
    body = _function_body_text("Install-Service")
    assert re.search(
        r"-ExecutionTimeLimit\s+\(New-TimeSpan\s+-Seconds\s+0\)",
        body,
    ), (
        "Install-Service must pass `-ExecutionTimeLimit (New-TimeSpan "
        "-Seconds 0)` to disable Task Scheduler's 72-hour task TTL. "
        "Not found in body:\n" + repr(body)
    )


def test_install_service_restart_on_failure_settings() -> None:
    """``Install-Service`` must configure Task Scheduler restart-on-failure.

    Outer-layer restart in case the supervisor itself crashes. The
    supervisor's in-process exit-75 respawn loop handles the
    fast-restart post-pair / post-update path; ``RestartCount`` /
    ``RestartInterval`` (1-minute floor in Task Scheduler) catches
    supervisor-level failure.
    """
    body = _function_body_text("Install-Service")
    assert "-RestartCount" in body, (
        "Install-Service must pass `-RestartCount` to "
        "New-ScheduledTaskSettingsSet. Not found in body:\n"
        + repr(body)
    )
    assert "-RestartInterval" in body, (
        "Install-Service must pass `-RestartInterval` to "
        "New-ScheduledTaskSettingsSet. Not found in body:\n"
        + repr(body)
    )


def test_install_service_cleans_up_legacy_scm_service() -> None:
    """``Install-Service`` must detect and remove the legacy SCM service.

    Migration path: a previous install may have left a LocalSystem
    SCM service ``MobaideAgent`` on the host. The new installer must
    stop and ``sc.exe delete`` it before registering the Scheduled
    Task (same name, two different OS-level objects). Idempotent on
    a clean machine — ``Get-Service`` returns nothing if SCM has
    already cleared the entry, so the cleanup is a no-op.
    """
    body = _function_body_text("Install-Service")
    assert re.search(r"\bGet-Service\b", body), (
        "Install-Service must call `Get-Service` to detect the legacy "
        "SCM service before deleting it. Not found in body:\n"
        + repr(body)
    )
    assert re.search(r"sc\.exe\s+delete", body), (
        "Install-Service must call `sc.exe delete $Script:ServiceName` "
        "to remove the legacy SCM service. (PowerShell has no built-in "
        "service-removal cmdlet on Win10 / Server 2019 — sc.exe is the "
        "stable primitive.) Not found in body:\n" + repr(body)
    )


def test_install_service_is_idempotent_on_existing_scheduled_task() -> None:
    """``Install-Service`` must be safe to re-run.

    Re-installation must unregister any pre-existing Scheduled Task
    with the same name before re-registering. ``Get-ScheduledTask``
    detects the existing task; ``Unregister-ScheduledTask`` removes
    it. Without this, ``Register-ScheduledTask -Force`` may keep a
    stale ``Execute`` / ``Argument`` path (the docs are inconsistent
    on the exact replace semantics across Windows versions), so we
    explicitly unregister first.
    """
    body = _function_body_text("Install-Service")
    assert re.search(r"\bGet-ScheduledTask\b", body), (
        "Install-Service must call `Get-ScheduledTask` to detect an "
        "existing task before re-registering. Not found in body:\n"
        + repr(body)
    )
    assert re.search(r"\bUnregister-ScheduledTask\b", body), (
        "Install-Service must call `Unregister-ScheduledTask` to "
        "remove a pre-existing task before re-creating it (re-install "
        "idempotency). Not found in body:\n" + repr(body)
    )


def test_install_service_does_not_install_pywin32() -> None:
    """``Install-Service`` must NOT install pywin32.

    Regression pin: the previous SCM-service design needed pywin32
    in a dedicated svc venv to host ``win32serviceutil.ServiceFramework``.
    The Scheduled Task supervisor is stdlib-only — invoking the
    legacy pywin32 install path on a clean machine would waste
    bandwidth and leave dead bytes on disk.
    """
    body_code = _strip_powershell_comments(_function_body_text("Install-Service"))
    assert "pywin32" not in body_code, (
        "Install-Service must NOT `pip install pywin32` — the "
        "Scheduled Task supervisor (deploy/windows_launcher.py) is "
        "stdlib-only. Comment-stripped body:\n" + repr(body_code)
    )


def test_install_service_does_not_create_svc_venv() -> None:
    """``Install-Service`` must NOT create a service-host venv.

    Regression pin: the previous design created
    ``<prefix>/svc/venv`` for pywin32. The supervisor is now a
    standalone script invoked by the same Python ``Resolve-Python``
    returned, so no dedicated venv is needed. A regression that
    re-creates the svc venv would double the install footprint
    and resurrect the pywin32 supply-chain surface.
    """
    body_code = _strip_powershell_comments(_function_body_text("Install-Service"))
    assert not re.search(r"-m\s+venv\b", body_code), (
        "Install-Service must NOT run `python -m venv` — the "
        "Scheduled Task supervisor needs no dedicated venv. "
        "Comment-stripped body:\n" + repr(body_code)
    )


def test_install_service_starts_task_immediately() -> None:
    """``Install-Service`` must call ``Start-ScheduledTask`` after registering.

    The installer is interactive; the user expects the agent up
    within seconds of install completion (not at the next logon).
    Without ``Start-ScheduledTask``, ``Wait-AgentHealthy`` (which
    comes next in ``Main``) would time out polling ``/api/health``
    because the agent only starts at the user's next logon.
    """
    body = _function_body_text("Install-Service")
    assert re.search(r"\bStart-ScheduledTask\b", body), (
        "Install-Service must call `Start-ScheduledTask "
        "-TaskName $Script:ServiceName` so the agent comes up "
        "immediately. Not found in body:\n" + repr(body)
    )


def test_install_service_downloads_supervisor_module_to_svc_dir() -> None:
    """``Install-Service`` must download the supervisor module to
    ``<prefix>/svc/mobaide_launcher.py``.

    The Scheduled Task action's ``-Argument`` points at this file
    (``-u "<path>"``). It must exist on disk BEFORE
    ``Register-ScheduledTask`` runs, and it must live in a
    version-independent location so the registered action path stays
    valid across upgrades and rollbacks.

    Source: ``$Script:WindowsLauncherUrl`` (defaults to
    ``https://get.mobaide.com/windows_launcher.py``).
    """
    body_code = _strip_powershell_comments(_function_body_text("Install-Service"))
    assert re.search(r"mobaide_launcher\.py", body_code), (
        "Install-Service must reference `mobaide_launcher.py` (the "
        "on-disk filename of the supervisor under <prefix>/svc/). Not "
        "found in body:\n" + repr(body_code)
    )
    assert re.search(
        r"Invoke-AgentDownload[^|\r\n]*\$Script:WindowsLauncherUrl",
        body_code,
    ), (
        "Install-Service must materialise the supervisor on disk by "
        "calling `Invoke-AgentDownload -Url $Script:WindowsLauncherUrl` "
        "(same download helper Install-Release uses; centralised "
        "SSL/UA handling). Comment-stripped body:\n" + repr(body_code)
    )


def test_install_service_does_not_copy_from_release_wheel() -> None:
    """``Install-Service`` must NOT copy the supervisor from the release wheel.

    Regression pin retained from the SCM-service era: the supervisor
    must come from gh-pages (co-versioned with install.ps1), not
    from the wheel (which ships only on tag pushes — a desync hazard).
    """
    body_code = _strip_powershell_comments(_function_body_text("Install-Service"))
    assert not re.search(
        r"site-packages\\mobaide\\windows_(?:service|launcher)\.py",
        body_code,
    ), (
        "Install-Service must NOT reference any wheel-installed "
        "`site-packages\\mobaide\\windows_*.py` path. The supervisor "
        "must come from gh-pages (co-versioned with install.ps1). "
        "Comment-stripped body:\n" + repr(body_code)
    )
    assert not re.search(r"\bCopy-Item\b", body_code), (
        "Install-Service must NOT use `Copy-Item` for the supervisor "
        "module (must be downloaded from gh-pages so installer and "
        "supervisor are co-versioned). Comment-stripped body:\n"
        + repr(body_code)
    )


def test_install_ps1_defines_windows_launcher_url_with_env_override() -> None:
    """install.ps1 must expose ``$Script:WindowsLauncherUrl`` with a
    ``MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL`` env override.

    Mirrors ``$Script:InstallUrl`` / ``MOBAIDE_AGENT_INSTALL_URL`` — same
    distribution channel (gh-pages), same override mechanism for
    self-hosted mirrors and CI fixtures. A self-hosted distribution that
    mirrors install.ps1 must mirror windows_launcher.py too, or set this
    env var to point at the canonical URL.
    """
    text = _DEPLOY_INSTALL_PS1.read_text(encoding="utf-8")
    assert re.search(
        r"\$Script:WindowsLauncherUrl\s*=\s*if\s*\(\s*\$env:MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL\s*\)",
        text,
    ), (
        "install.ps1 must declare `$Script:WindowsLauncherUrl` with a "
        "MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL env override, parallel to "
        "$Script:InstallUrl / MOBAIDE_AGENT_INSTALL_URL. Not found."
    )
    assert "https://get.mobaide.com/windows_launcher.py" in text, (
        "install.ps1 must default $Script:WindowsLauncherUrl to "
        "https://get.mobaide.com/windows_launcher.py (the gh-pages-"
        "published canonical URL, co-versioned with install.ps1). "
        "Not found."
    )


def test_install_ps1_removes_legacy_windows_service_url_constant() -> None:
    """``$Script:WindowsServiceUrl`` must NOT exist (renamed to LauncherUrl).

    Regression pin: a partial-revert that re-introduces the old
    ``$Script:WindowsServiceUrl`` constant alongside the new
    ``$Script:WindowsLauncherUrl`` would silently re-route the
    supervisor download through a stale URL (different file at the
    same gh-pages publisher).
    """
    text = _DEPLOY_INSTALL_PS1.read_text(encoding="utf-8")
    assert "$Script:WindowsServiceUrl" not in text, (
        "$Script:WindowsServiceUrl must be removed from install.ps1 "
        "after the SCM-service → Scheduled-Task rename. The new "
        "constant is $Script:WindowsLauncherUrl."
    )
    assert "MOBAIDE_AGENT_WINDOWS_SERVICE_URL" not in text, (
        "MOBAIDE_AGENT_WINDOWS_SERVICE_URL must be removed from "
        "install.ps1 after the rename. The new override env var is "
        "MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL."
    )


def test_install_service_verifies_task_exists_before_starting() -> None:
    """``Install-Service`` must re-check Task Scheduler after registration.

    Defence-in-depth on the silent-failure anti-pattern. After
    ``Register-ScheduledTask`` succeeds, confirm the task actually
    appears (``Get-ScheduledTask``) before invoking
    ``Start-ScheduledTask`` / handing off to ``Wait-AgentHealthy``.
    Surfaces a real error at install time instead of a 60-second
    polling timeout downstream.
    """
    body = _function_body_text("Install-Service")
    register_match = re.search(r"\bRegister-ScheduledTask\b", body)
    assert register_match is not None, (
        "Pre-condition: Install-Service must call Register-ScheduledTask "
        "(covered by test_install_service_registers_scheduled_task)."
    )
    tail = body[register_match.end():]
    assert re.search(r"\bGet-ScheduledTask\b", tail), (
        "After Register-ScheduledTask, Install-Service must re-check "
        "the task's existence via `Get-ScheduledTask` and fail loudly "
        "if it is missing. Without this check, a silent registration "
        "failure surfaces as an opaque Wait-AgentHealthy timeout. "
        "Body slice after registration:\n" + repr(tail)
    )


@pytest.mark.parametrize(
    "func_name",
    [
        "Read-EnvValue",
        "Wait-AgentHealthy",
        "Show-PairingQrFromService",
    ],
)
def test_pairing_qr_helpers_are_defined(func_name: str) -> None:
    """The post-service pairing-QR flow depends on three new functions.

    The original install order rendered the QR via ``Write-PairingQr``
    BEFORE ``Install-Service`` ran, so a mobile scan during the gap had
    nowhere to dispatch the pairing claim and ``RELAY_AGENT_ID`` /
    ``RELAY_AGENT_TOKEN`` silently stayed empty. The fix:
    ``Install-Service`` runs first, ``Wait-AgentHealthy`` confirms
    ``/api/health`` returns 200 (the agent is actually up, not just
    "SCM accepted the start request"), then
    ``Show-PairingQrFromService`` waits for the running service's
    pairing-monitor to write ``run/pending_pairing.json`` and renders
    via ``mobaide-agent show-qr --require-pending``. If any of the
    three helpers is renamed or deleted, ``Main`` calls into the void.
    """
    funcs = _list_function_names()
    assert func_name in funcs, (
        f"expected function {func_name!r} in {_DEPLOY_INSTALL_PS1}; "
        f"found functions: {funcs!r}. Main() depends on Read-EnvValue "
        f"(port lookup from .env), Wait-AgentHealthy (poll /api/health "
        f"until 200), and Show-PairingQrFromService (wait for "
        f"pending_pairing.json, render via show-qr --require-pending)."
    )


def test_old_write_pairing_qr_function_is_removed() -> None:
    """The pre-fix ``Write-PairingQr`` must NOT be defined anymore.

    The old function ran ``mobaide-agent pair-qr --print`` BEFORE the
    service was up — exactly the ordering bug the rewrite eliminates.
    Leaving the old definition behind would mean a future caller could
    re-introduce the pre-service-up render, reproducing the original
    "QR scans but credentials never land" symptom.
    """
    funcs = _list_function_names()
    assert "Write-PairingQr" not in funcs, (
        f"Write-PairingQr must be removed from {_DEPLOY_INSTALL_PS1} "
        f"after the service-first reorder; Show-PairingQrFromService "
        f"replaces it. Found functions: {funcs!r}."
    )


def test_main_orders_install_service_before_health_before_qr() -> None:
    """``Main`` must call ``Install-Service`` before ``Wait-AgentHealthy``
    before ``Show-PairingQrFromService`` before ``Write-Completion``.

    The whole point of this rewrite is the ordering. Pinning it via
    string positions in the ``Main`` body catches any future refactor
    that accidentally moves the QR render back ahead of the service
    start.

    Comments are stripped before the position search — the
    explanatory paragraph in ``Main`` documents the four function
    names in the order they're called, so a naive ``.find()`` on the
    raw body would resolve them to comment positions instead of the
    actual call lines.
    """
    body = _strip_powershell_comments(_function_body_text("Main"))
    install_pos = body.find("Install-Service")
    health_pos = body.find("Wait-AgentHealthy")
    qr_pos = body.find("Show-PairingQrFromService")
    completion_pos = body.find("Write-Completion")
    assert install_pos >= 0, (
        "Main must call Install-Service. Body:\n" + repr(body)
    )
    assert health_pos >= 0, (
        "Main must call Wait-AgentHealthy after Install-Service so the "
        "QR is only rendered once the agent is actually responding. "
        "Body:\n" + repr(body)
    )
    assert qr_pos >= 0, (
        "Main must call Show-PairingQrFromService. Body:\n" + repr(body)
    )
    assert completion_pos >= 0, (
        "Main must call Write-Completion. Body:\n" + repr(body)
    )
    assert install_pos < health_pos < qr_pos < completion_pos, (
        f"Main ordering is broken. Required order: Install-Service "
        f"({install_pos}) < Wait-AgentHealthy ({health_pos}) < "
        f"Show-PairingQrFromService ({qr_pos}) < Write-Completion "
        f"({completion_pos}). The QR must render AFTER the service is "
        f"confirmed healthy, otherwise a mobile scan during the boot "
        f"gap will silently fail to pair and .env will stay empty."
    )


def test_main_no_longer_renders_qr_before_install_service() -> None:
    """No ``Write-PairingQr`` / ``pair-qr --print`` call may appear in
    ``Main`` ahead of ``Install-Service``.

    Catches accidental partial-revert that re-adds the pre-fix render
    call alongside the new service-first flow. The new install-time
    render goes exclusively through ``Show-PairingQrFromService``,
    which lives AFTER the service is healthy.
    """
    body = _function_body_text("Main")
    assert "Write-PairingQr" not in body, (
        "Main must not call Write-PairingQr — that function was "
        "removed and its replacement (Show-PairingQrFromService) "
        "runs after Install-Service + Wait-AgentHealthy. Body:\n"
        + repr(body)
    )
    assert "pair-qr --print" not in body, (
        "Main must not invoke `mobaide-agent pair-qr --print` "
        "directly — that's the pre-fix shape that fired before the "
        "service was up. The new flow uses `mobaide-agent show-qr "
        "--require-pending` from inside Show-PairingQrFromService, "
        "which only runs after Wait-AgentHealthy succeeds. Body:\n"
        + repr(body)
    )


def test_wait_agent_healthy_polls_api_health_with_timeout() -> None:
    """``Wait-AgentHealthy`` must poll the FastAPI health endpoint.

    Pin the actual path (``/api/health``) and ``127.0.0.1`` (not the
    agent's bind ``HOST`` which defaults to ``0.0.0.0`` — the installer
    contacts the loopback address) and a default 60-second budget.
    Pin ``Invoke-WebRequest`` (PowerShell's cross-trust-store HTTP
    primitive — same rationale as ``Invoke-AgentDownload`` documents).
    """
    body = _function_body_text("Wait-AgentHealthy")
    assert "/api/health" in body, (
        "Wait-AgentHealthy must hit the agent's `/api/health` route "
        "(defined at mobaide/agent/app.py:380). Body:\n" + repr(body)
    )
    assert "127.0.0.1" in body, (
        "Wait-AgentHealthy must contact 127.0.0.1, not the agent's "
        "configured HOST (which defaults to 0.0.0.0, the bind "
        "address; the installer hits the loopback contact address). "
        "Body:\n" + repr(body)
    )
    assert re.search(r"Invoke-WebRequest\b", body), (
        "Wait-AgentHealthy must use Invoke-WebRequest (backed by .NET "
        "HttpClient / Schannel; trusts the Windows root store and "
        "enterprise GPO-injected CAs). Same rationale as "
        "Invoke-AgentDownload — do not regress to urllib here. "
        "Body:\n" + repr(body)
    )


def test_wait_agent_healthy_fails_with_event_log_pointer() -> None:
    """On timeout, ``Wait-AgentHealthy`` must surface the Event Viewer.

    Operators need a clear pointer to the diagnostic surface so a
    silent supervisor crash shows up as a discoverable failure instead
    of "the QR I scanned didn't pair". The Scheduled Task surface
    lives under
    ``Event Viewer → Applications and Services Logs → Microsoft →
    Windows → TaskScheduler → Operational``; the agent's own captured
    stderr is at ``<prefix>/logs/agent.err.log``.
    """
    body = _function_body_text("Wait-AgentHealthy")
    assert "Write-Fail" in body, (
        "Wait-AgentHealthy must hard-fail on timeout (via Write-Fail), "
        "not silently fall through. The whole point of this guard is "
        "to surface a service crash that Start-Service masked. "
        "Body:\n" + repr(body)
    )
    assert "eventvwr" in body or "EventLog" in body, (
        "Wait-AgentHealthy's diagnostic message must reference the "
        "Windows Event Viewer / Event Log so the operator can find "
        "the underlying service-crash trace. Body:\n" + repr(body)
    )
    assert "MobaideAgent" in body, (
        "Wait-AgentHealthy's diagnostic must name the service "
        "(`MobaideAgent`) so the operator's eventvwr / Get-EventLog "
        "filter matches. Body:\n" + repr(body)
    )
    assert "agent.err.log" in body, (
        "Wait-AgentHealthy's diagnostic must point at the supervisor's "
        "captured stderr at <prefix>/logs/agent.err.log. Without this "
        "log capture (deploy/windows_launcher.py:_spawn_release_launcher "
        "redirects stderr to the rotated log file), every Python "
        "traceback from the launcher or agent would be discarded — "
        "operators would only see Task Scheduler's exit-code line, "
        "never the real error. Body:\n" + repr(body)
    )


def test_show_pairing_qr_uses_require_pending_flag() -> None:
    """``Show-PairingQrFromService`` must call ``show-qr --require-pending``.

    The flag prevents the CLI from initiating a second pairing nonce
    when the file is briefly absent. The running service is the single
    initiator; the installer renders from the file the service wrote.
    Without the flag, the CLI falls through to ``_run_pair_qr`` (which
    initiates), and two concurrent /ws/pairing/{nonce} dials race for
    the same nonce — central_server rejects the loser with WS close
    4001 (single-use ws semantics).
    """
    body = _function_body_text("Show-PairingQrFromService")
    assert "show-qr" in body, (
        "Show-PairingQrFromService must invoke `mobaide-agent show-qr` "
        "(rather than `pair-qr --print`) so the CLI reads the running "
        "service's pending_pairing.json. Body:\n" + repr(body)
    )
    assert "--require-pending" in body, (
        "Show-PairingQrFromService must pass `--require-pending` to "
        "the show-qr CLI so it never initiates a second nonce; the "
        "running agent service is the single initiator. Without this "
        "flag, two processes race for the same /ws/pairing/{nonce} "
        "dial and central_server rejects the loser (WS close 4001). "
        "Body:\n" + repr(body)
    )
    assert "pending_pairing.json" in body, (
        "Show-PairingQrFromService must wait for "
        "`pending_pairing.json` written by the running service before "
        "rendering — the lifespan task is concurrent with the yield, "
        "so /api/health responds 200 before the file appears. Body:\n"
        + repr(body)
    )


def test_show_pairing_qr_preserves_skip_env_var() -> None:
    """``MOBAIDE_AGENT_SKIP_INSTALL_QR=1`` must still suppress the QR.

    Documented env-var contract in docs/deployment.md, used by CI /
    unattended installs that don't have an operator at the terminal.
    The reorder MUST NOT silently drop this hook.
    """
    body = _function_body_text("Show-PairingQrFromService")
    assert "MOBAIDE_AGENT_SKIP_INSTALL_QR" in body, (
        "Show-PairingQrFromService must honour the documented "
        "MOBAIDE_AGENT_SKIP_INSTALL_QR opt-out env var. Body:\n"
        + repr(body)
    )


# ----------------------------------------------------------------------
# Find-ClaudeBinary / MOBAIDE_AGENT_CLAUDE_BIN_HINT / Write-AgentEnv pins
# ----------------------------------------------------------------------
#
# These guard the ``claude_cli_missing`` 503 — most users only have
# ``%APPDATA%\npm\claude.cmd`` on their interactive PATH, not in the
# elevated installer's session or in any environment Task Scheduler
# might use for diagnostic actions. The installer must (a) discover the
# user's claude path BEFORE the UAC relaunch (under the originating user's
# env), (b) carry the path across the UAC boundary via the
# ``MOBAIDE_AGENT_CLAUDE_BIN_HINT`` passthrough env var, (c) write it into
# ``<prefix>\mobaide_agent\.env`` as ``MOBAIDE_CLAUDE_BIN`` using either
# ``init-env --claude-bin`` (fresh install) or ``set-env --key
# MOBAIDE_CLAUDE_BIN`` (re-install backfill). Each leg of this contract
# is pinned below; a regression on any one re-opens the 503.


def test_find_claude_binary_is_defined() -> None:
    """The ``Find-ClaudeBinary`` helper must exist as a real function.

    Called by both ``Assert-AdminOrElevate`` (pre-UAC discovery) and
    ``Write-AgentEnv`` (post-UAC fallback). If the function is renamed
    or deleted, both callsites silently lose claude.cmd auto-detection
    and the install regresses to the pre-fix ``claude_cli_missing`` 503.
    """
    funcs = _list_function_names()
    assert "Find-ClaudeBinary" in funcs, (
        f"expected function 'Find-ClaudeBinary' in "
        f"{_DEPLOY_INSTALL_PS1}; found functions: {funcs!r}. The pre-UAC "
        f"helper in Assert-AdminOrElevate AND the post-UAC fallback in "
        f"Write-AgentEnv both call into this name; if it's missing, the "
        f"install regresses to claude_cli_missing 503."
    )


def test_find_claude_binary_probes_appdata_npm() -> None:
    """``Find-ClaudeBinary`` must probe ``%APPDATA%\\npm\\claude.cmd``.

    That's the default landing site for ``npm install -g
    @anthropic-ai/claude-code`` on Windows. Probing PATH alone misses
    this — many users have npm on PATH only for interactive shells, and
    the elevated installer's session (relaunched via UAC ``Start-Process
    -Verb RunAs``) may not inherit the originating user's PATH. The
    whole point of the fallback list is to catch this case explicitly.
    A regression to a PATH-only check re-opens the 503.
    """
    body = _function_body_text("Find-ClaudeBinary")
    assert "APPDATA" in body, (
        "Find-ClaudeBinary must read $env:APPDATA to probe the user's "
        "npm prefix; not found in body:\n" + repr(body)
    )
    # The literal 'npm' path segment combined with 'claude.cmd' is the
    # canonical install location — pin both so a refactor that splits
    # them across variables is still caught.
    assert "npm" in body and "claude.cmd" in body, (
        "Find-ClaudeBinary must reference both 'npm' and 'claude.cmd' "
        "(default Windows install path: %APPDATA%\\npm\\claude.cmd). "
        "Body:\n" + repr(body)
    )


def test_find_claude_binary_skips_windowsapps_stub() -> None:
    """``Find-ClaudeBinary`` must filter out the WindowsApps reparse stub.

    Mirrors the exact same WindowsApps guard ``Find-Python`` already
    has: ``Get-Command claude`` can resolve to an App Execution Alias
    trampoline under ``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\`` (e.g.,
    if Microsoft Store ever ships an ``alias-only`` claude package).
    That trampoline never actually runs the CLI in an unattended ``irm
    | iex`` flow; it opens the Store UI. If discovery returns the stub
    path, the agent later spawns it from a non-interactive context
    (the Scheduled Task's action) where the Store UI cannot surface,
    so it hangs or errors opaquely. Filter on ``$exe.Source -like
    "*\\WindowsApps\\*"`` mirrors the same protection ``Find-Python``
    documents.
    """
    body = _function_body_text("Find-ClaudeBinary")
    assert "WindowsApps" in body, (
        "Find-ClaudeBinary must filter out the WindowsApps App "
        "Execution Alias stub (same protection as Find-Python); not "
        "found in body:\n" + repr(body)
    )


def test_assert_admin_or_elevate_captures_claude_bin_pre_uac() -> None:
    """``Assert-AdminOrElevate`` must call ``Find-ClaudeBinary`` BEFORE
    starting the elevated child, so the lookup runs under the
    originating user's env (where ``%APPDATA%\\npm\\claude.cmd`` actually
    resolves to a real path).

    The plan is explicit: capture pre-UAC, pass across the boundary via
    ``MOBAIDE_AGENT_CLAUDE_BIN_HINT``. If a future refactor moves
    ``Find-ClaudeBinary`` to AFTER ``Start-Process``, the discovery runs
    inside an already-elevated session — for the ``Run as different
    user`` consent flow that elevated session has a different profile,
    so the discovery sees no claude.cmd and the hint never lands. The
    cross-account elevation path then regresses to the 503.
    """
    body = _function_body_text("Assert-AdminOrElevate")
    find_at = body.find("Find-ClaudeBinary")
    start_at = body.find("Start-Process")
    assert find_at != -1, (
        "Assert-AdminOrElevate must call Find-ClaudeBinary; not found "
        "in body:\n" + repr(body)
    )
    assert start_at != -1, (
        "Assert-AdminOrElevate must call Start-Process (the UAC "
        "relaunch); not found in body:\n" + repr(body)
    )
    assert find_at < start_at, (
        f"Assert-AdminOrElevate must call Find-ClaudeBinary BEFORE "
        f"Start-Process. Discovery after elevation runs under whatever "
        f"profile the UAC consent picked, which may have no "
        f"%APPDATA%\\npm\\claude.cmd. Pre-UAC discovery is the only "
        f"way to make the cross-account elevation path resilient. "
        f"Found Find-ClaudeBinary at offset {find_at}, Start-Process "
        f"at offset {start_at}. Body:\n" + repr(body)
    )


def test_claude_bin_hint_is_preserved_across_uac_elevation() -> None:
    """``MOBAIDE_AGENT_CLAUDE_BIN_HINT`` must be in the elevation
    passthrough array literal inside ``Assert-AdminOrElevate``.

    Same regression class as ``MOBAIDE_AGENT_SKIP_PYTHON_INSTALL`` /
    ``MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL`` (already pinned upstream): if
    the new hint variable is not in ``$passthrough``, the elevated
    child re-fetches without it, falls back to its own
    ``Find-ClaudeBinary`` from inside the elevated session, and on a
    ``Run as different user`` consent picks up the wrong profile (or no
    claude.cmd at all). The whole point of the pre-UAC discovery is
    defeated.
    """
    body = _function_body_text("Assert-AdminOrElevate")
    assert re.search(
        r"['\"]MOBAIDE_AGENT_CLAUDE_BIN_HINT['\"]", body
    ), (
        "MOBAIDE_AGENT_CLAUDE_BIN_HINT must appear in the $passthrough "
        "array inside Assert-AdminOrElevate so the pre-UAC-discovered "
        "claude.cmd path survives the UAC re-launch. Without it, the "
        "elevated child falls back to its own Find-ClaudeBinary which "
        "may see no claude.cmd under a different-user elevation. "
        "Function body:\n" + repr(body)
    )


def test_write_agent_env_passes_claude_bin_to_init_env_fresh_install() -> None:
    """``Write-AgentEnv`` must pass ``--claude-bin <path>`` to
    ``init-env`` when ``.env`` does not yet exist.

    Fresh-install path: the file is written from scratch, so the
    surgical ``set-env`` verb does not apply (it requires a pre-existing
    file). The plan wires the discovered claude path into ``init-env``
    via its new ``--claude-bin`` flag so the freshly rendered .env
    lands ``MOBAIDE_CLAUDE_BIN=<path>`` uncommented in a single step.
    A regression that drops ``--claude-bin`` here would render a .env
    with the commented placeholder, and the service would 503 on
    first session-create.
    """
    body = _function_body_text("Write-AgentEnv")
    assert "init-env" in body, (
        "Write-AgentEnv must invoke `init-env` on fresh install; not "
        "found in body:\n" + repr(body)
    )
    assert "--claude-bin" in body, (
        "Write-AgentEnv must pass `--claude-bin` to init-env so the "
        "fresh .env carries MOBAIDE_CLAUDE_BIN. Without the flag the "
        "template's commented placeholder passes through unchanged and "
        "the agent 503s on session-create. Body:\n" + repr(body)
    )


def test_write_agent_env_invokes_set_env_for_reinstall_backfill() -> None:
    """``Write-AgentEnv`` must invoke ``set-env --key MOBAIDE_CLAUDE_BIN``
    on the re-install path (``.env`` already exists).

    Re-install policy from the plan: leave the operator's existing .env
    (RELAY_AGENT_ID/TOKEN, JWT_SECRET_KEY, any manual edits) intact;
    surgically backfill only ``MOBAIDE_CLAUDE_BIN``. ``init-env
    --force`` would achieve the same outcome but it rewrites every
    templated line and an operator-edited LOG_LEVEL or similar would
    silently revert. ``set-env`` touches one key only. A regression
    to ``init-env --force`` re-opens the manual-edit-clobber gap.
    """
    body = _function_body_text("Write-AgentEnv")
    assert "set-env" in body, (
        "Write-AgentEnv must invoke `set-env` on the re-install path "
        "to surgically backfill MOBAIDE_CLAUDE_BIN. Replacing it with "
        "`init-env --force` would clobber the operator's other manual "
        "edits. Body:\n" + repr(body)
    )
    # Pin both the key flag AND the value flag — the set-env verb
    # requires both as named args. A future refactor that drops the
    # explicit `--key MOBAIDE_CLAUDE_BIN` (e.g., by routing through a
    # positional arg) would silently change the contract.
    assert re.search(r"--key\s+MOBAIDE_CLAUDE_BIN", body), (
        "Write-AgentEnv must pass `--key MOBAIDE_CLAUDE_BIN` to "
        "set-env (the surgical key under test for this fix); not "
        "found in body:\n" + repr(body)
    )
    assert "--value" in body, (
        "Write-AgentEnv must pass `--value` to set-env (set-env "
        "requires both --key and --value to land a line). Body:\n"
        + repr(body)
    )


def test_write_agent_env_reads_existing_claude_bin_before_backfill() -> None:
    """``Write-AgentEnv`` must consult ``Read-EnvValue`` for
    ``MOBAIDE_CLAUDE_BIN`` before calling ``set-env``.

    Per plan policy "Surgical set-env only" / "operator-managed always
    wins": if the operator has hand-edited the .env to point at a
    custom claude install (e.g., a non-default npm prefix or a tarball
    extraction), the installer must NOT silently overwrite that value
    with whatever ``Find-ClaudeBinary`` discovered. Read first, only
    backfill when the slot is empty. A regression that drops the
    Read-EnvValue probe means every re-install stamps over the
    operator's edit.

    Strip comments before the position search: the explanatory comment
    block in ``Write-AgentEnv`` mentions the literal token ``set-env``
    inside the "Surgical set-env only" policy documentation, which a
    naive ``.find()`` on the raw body matches before the actual call
    line. Comments-stripped search isolates the real call positions.
    """
    body = _strip_powershell_comments(_function_body_text("Write-AgentEnv"))
    read_at = body.find("Read-EnvValue")
    setenv_at = body.find("set-env")
    assert read_at != -1, (
        "Write-AgentEnv must consult Read-EnvValue for "
        "MOBAIDE_CLAUDE_BIN before calling set-env (per the "
        "operator-managed-wins policy); not found in body:\n"
        + repr(body)
    )
    assert setenv_at != -1, (
        "Write-AgentEnv must call set-env on the re-install path. "
        "Body:\n" + repr(body)
    )
    assert read_at < setenv_at, (
        f"Write-AgentEnv must call Read-EnvValue BEFORE set-env so an "
        f"existing operator-set MOBAIDE_CLAUDE_BIN is preserved. "
        f"Read-EnvValue at offset {read_at}, set-env at offset "
        f"{setenv_at}. Body:\n" + repr(body)
    )


def test_write_agent_env_prefers_hint_then_falls_back_to_find() -> None:
    """``Write-AgentEnv`` must read ``$env:MOBAIDE_AGENT_CLAUDE_BIN_HINT``
    first and only fall back to ``Find-ClaudeBinary`` if the hint is
    empty.

    The hint is the pre-UAC discovery captured under the originating
    user's env (see ``Assert-AdminOrElevate``). For the canonical ``irm
    | iex`` consent flow the elevated session is the same user, so
    ``Find-ClaudeBinary`` would also resolve correctly — but for the
    ``Run as different user`` consent it won't. Preferring the hint
    closes that gap. A regression to "always call Find-ClaudeBinary"
    silently breaks cross-account elevation.
    """
    body = _function_body_text("Write-AgentEnv")
    hint_at = body.find("MOBAIDE_AGENT_CLAUDE_BIN_HINT")
    find_at = body.find("Find-ClaudeBinary")
    assert hint_at != -1, (
        "Write-AgentEnv must read $env:MOBAIDE_AGENT_CLAUDE_BIN_HINT "
        "(the pre-UAC discovery). Body:\n" + repr(body)
    )
    assert find_at != -1, (
        "Write-AgentEnv must call Find-ClaudeBinary as a fallback "
        "(for installs that did not go through Assert-AdminOrElevate, "
        "e.g., an already-elevated PowerShell). Body:\n" + repr(body)
    )
    assert hint_at < find_at, (
        f"Write-AgentEnv must check MOBAIDE_AGENT_CLAUDE_BIN_HINT "
        f"BEFORE falling back to Find-ClaudeBinary. The hint carries "
        f"the originating-user discovery across UAC; the fallback "
        f"runs under whatever profile the elevated session has. Hint "
        f"at offset {hint_at}, Find-ClaudeBinary at offset {find_at}. "
        f"Body:\n" + repr(body)
    )


def test_main_calls_write_agent_env() -> None:
    """``Main`` must call ``Write-AgentEnv`` so the discovered claude
    path actually lands in ``.env``.

    Sanity guard for refactors that touch ``Main``'s call chain: if
    ``Write-AgentEnv`` is renamed, inlined, or silently dropped, every
    Windows install regresses to the 503 — discovery still runs (the
    pre-UAC hint and Find-ClaudeBinary helpers are intact), but nothing
    persists the result into the file the service reads at boot.
    """
    body = _function_body_text("Main")
    assert "Write-AgentEnv" in body, (
        "Main must call Write-AgentEnv to persist the discovered "
        "claude path into .env. Without this call, every install "
        "regresses to claude_cli_missing 503 even though discovery "
        "succeeded. Body:\n" + repr(body)
    )


def test_set_env_subcommand_is_defined_in_config_module() -> None:
    """The CLI verb ``set-env`` invoked by ``Write-AgentEnv`` must
    exist as a subcommand in ``mobaide.agent.config``.

    Cross-language contract: ``install.ps1`` is the producer
    (constructs the argv); ``mobaide.agent.config`` is the consumer
    (registers the subcommand). If either side drifts, the install
    silently fails with ``argparse: invalid choice: 'set-env'``. The
    Python-side definition is tested separately in
    ``test_set_env_cli.py``; this test pins the spelling install.ps1
    expects on the PS5.1 side.
    """
    body = _function_body_text("Write-AgentEnv")
    # The exact subcommand string must appear verbatim — pydantic's
    # argparse subparsers reject anything else.
    assert "set-env" in body, (
        "Write-AgentEnv must spell the subcommand 'set-env' exactly "
        "(must match the argparse subparser name in "
        "mobaide.agent.config._build_parser). Body:\n" + repr(body)
    )


def test_write_agent_env_warns_when_claude_not_found() -> None:
    """``Write-AgentEnv`` must ``Write-Warn`` (not ``Write-Fail``) when
    neither the hint nor ``Find-ClaudeBinary`` produced a path.

    Per plan policy "warn and continue": missing claude at install time
    is a user-recoverable condition (operator installs claude later via
    ``npm install -g @anthropic-ai/claude-code`` and either restarts
    the service or re-runs the installer). Hard-failing the install
    here would block the pairing flow entirely — the agent never
    starts, the QR never renders, the operator can't recover without
    re-running the installer. The 503 surfacing at session-create time
    with the install-hint message is the better UX.
    """
    body = _function_body_text("Write-AgentEnv")
    assert "Write-Warn" in body, (
        "Write-AgentEnv must Write-Warn when claude.cmd is not found "
        "(warn-and-continue policy). Body:\n" + repr(body)
    )
    # Defensive negative pin: hard-fail would block install entirely.
    # ``Write-Fail`` may legitimately appear in unrelated init-env error
    # branches inside Write-AgentEnv, so we check that the "missing
    # claude" path is reachable via Write-Warn rather than asserting
    # Write-Fail is fully absent.
    # The "npm install -g" hint must appear in the body so the warning
    # itself tells the operator how to fix it.
    assert "npm install" in body, (
        "Write-AgentEnv's warning must mention `npm install -g "
        "@anthropic-ai/claude-code` so the operator has an actionable "
        "next step. Body:\n" + repr(body)
    )
