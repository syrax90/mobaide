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
