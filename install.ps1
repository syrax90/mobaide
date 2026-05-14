# Mobaide agent install script (Windows).
#
# Usage:
#     irm https://get.mobaide.com/install.ps1 | iex
#
# Self-elevates via UAC if run unelevated (the SCM service registration and
# %ProgramFiles%\MobaideAgent writes both require admin). Auto-installs
# Python 3.12 via winget if no qualifying interpreter is on PATH.
#
# Idempotent: re-running upgrades the agent in place. Each release is staged
# into a sibling releases\<version>\ directory and the current_version
# pointer is rewritten atomically; the launcher boots into whichever release
# is current.
#
# Wrapped in a single Main function called once at the bottom so a partial
# download (irm | iex) cannot execute half a script.

$ErrorActionPreference = "Stop"

$Script:Prefix = if ($env:MOBAIDE_AGENT_PREFIX) {
    $env:MOBAIDE_AGENT_PREFIX
} else {
    Join-Path $env:ProgramFiles "MobaideAgent"
}
$Script:ManifestUrl = if ($env:MOBAIDE_AGENT_MANIFEST_URL) {
    $env:MOBAIDE_AGENT_MANIFEST_URL
} else {
    "https://api.mobaide.com/api/v1/agent/manifest.json"
}
$Script:TrustKeyB64 = $env:MOBAIDE_AGENT_TRUST_KEY_B64
# Self-host / loopback escape hatch for the trust-key fetch. Defaults to
# off (HTTPS-only); set MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1
# only on a trusted local network.
$Script:AllowInsecureTrustKeyFetch = ($env:MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH -eq "1")
$Script:ServiceName = "MobaideAgent"
# Used by Assert-AdminOrElevate to re-fetch this script under UAC. Override
# for self-hosted distributions of install.ps1.
$Script:InstallUrl = if ($env:MOBAIDE_AGENT_INSTALL_URL) {
    $env:MOBAIDE_AGENT_INSTALL_URL
} else {
    "https://get.mobaide.com/install.ps1"
}
# Service-host module URL. Co-versioned with install.ps1 itself: the
# publish-gh-pages workflow ships both files together on every push to
# main, so an installer change that depends on a new service-module API
# can never desync from the module it ships against. The previous design
# copied the module out of the installed wheel, which coupled installer
# changes to wheel re-tagging and produced the
# "Service module source not found at ...\releases\<v>\venv\..." failure.
$Script:WindowsServiceUrl = if ($env:MOBAIDE_AGENT_WINDOWS_SERVICE_URL) {
    $env:MOBAIDE_AGENT_WINDOWS_SERVICE_URL
} else {
    "https://get.mobaide.com/windows_service.py"
}
# Opt out of the auto-Python-install path for CI / locked-down hosts; the
# installer then falls back to the legacy "fail if no Python 3.12+" behaviour.
$Script:SkipPythonInstall = [bool]$env:MOBAIDE_AGENT_SKIP_PYTHON_INSTALL

function Write-Info  { param($Msg) Write-Host "[INFO]  $Msg" }
function Write-Warn  { param($Msg) Write-Warning $Msg }
function Write-Fail  { param($Msg) Write-Error $Msg; exit 1 }

# Pass a multi-line Python script to python.exe via a temp file rather than
# `-c <heredoc>`. Windows PowerShell 5.1 silently drops embedded " characters
# when wrapping a long script for a native .exe — CommandLineToArgvW then
# toggles in and out of quoted-region state at each stripped quote and joins
# the surrounding tokens, so a line like
#     sys.exit(f"...{x}")
# arrives at Python as
#     sys.exit(f...{x})
# and parses as a SyntaxError. Writing the script to disk eliminates the
# argv-quoting step entirely; the bash installer takes the equivalent path
# via heredocs to stdin. UTF-8 without BOM matches Python 3's PEP 3120
# default source encoding (Set-Content -Encoding UTF8 differs between PS 5.1
# and 7 — the .NET API is consistent across both).
function Invoke-PythonScript {
    param(
        [Parameter(Mandatory=$true)] $PythonExe,
        [Parameter(Mandatory=$true)] [string] $Script,
        [string[]] $Arguments = @()
    )
    $tmpScript = Join-Path $env:TEMP ("mobaide-py-" + [guid]::NewGuid() + ".py")
    [System.IO.File]::WriteAllText(
        $tmpScript, $Script, [System.Text.UTF8Encoding]::new($false)
    )
    try {
        & $PythonExe $tmpScript @Arguments
    } finally {
        Remove-Item -Force $tmpScript -ErrorAction SilentlyContinue
    }
}

# Centralized HTTPS asset download. Every download in the install pipeline
# goes through PowerShell's `Invoke-WebRequest` rather than Python's
# `urllib.request.urlopen`:
#
#   * The python.org Windows installer links OpenSSL with an empty bundled
#     CA path AND ships no "Install Certificates.command" equivalent. On
#     those interpreters `urllib.request.urlopen` falls back to
#     `ssl.SSLContext.load_default_certs()`, which reads the Windows ROOT
#     store but does NOT perform AIA (Authority Information Access) chasing
#     for missing intermediates (CPython bpo-18617). On hosts with a
#     stripped intermediate (common behind corporate MITM proxies,
#     freshly-built ROOT stores) this surfaces as
#     `SSL: CERTIFICATE_VERIFY_FAILED unable to get local issuer certificate`
#     even when the same TLS endpoint succeeds via Invoke-WebRequest.
#   * Invoke-WebRequest is backed by .NET HttpClient / Schannel, which
#     trusts the Windows root CA store including AD / enterprise CAs
#     injected by GPO — works on corporate hosts where urllib fails.
#
# PS 5.1's progress bar is notoriously slow for streaming downloads
# (PowerShell/PowerShell#2138), so we silence $ProgressPreference around
# the call. The `file://` branch preserves the air-gapped self-host path
# the old Python `fetch()` supported (test fixtures and offline mirrors).
function Invoke-AgentDownload {
    param(
        [Parameter(Mandatory=$true)] [string] $Url,
        [Parameter(Mandatory=$true)] [string] $OutFile,
        [string] $UserAgent,
        [int]    $TimeoutSec = 300
    )
    $uri = [System.Uri]$Url
    if ($uri.Scheme -eq 'file') {
        Copy-Item -LiteralPath $uri.LocalPath -Destination $OutFile -Force
        return
    }
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        $iwrArgs = @{
            Uri             = $Url
            OutFile         = $OutFile
            UseBasicParsing = $true
            TimeoutSec      = $TimeoutSec
        }
        if ($UserAgent) { $iwrArgs['UserAgent'] = $UserAgent }
        Invoke-WebRequest @iwrArgs
    } finally {
        $ProgressPreference = $oldProgress
    }
}

# Derive the canonical PEP 427 wheel filename from a `wheel_url`. Runs
# BEFORE the download so a malformed or hostile manifest fails fast
# (and the wheel lands at its canonical name directly — pip 25.2+
# enforces PEP 427 wheel-filename validation at install time, so a wheel
# staged under any other filename would be rejected one step later).
# The basename validation is defense-in-depth on top of the Ed25519
# manifest signature verify: a compromised signing key could otherwise
# smuggle a path-traversal payload through `wheel_url`.
function Get-WheelBasename {
    param([Parameter(Mandatory=$true)] [string] $WheelUrl)
    $uri = [System.Uri]$WheelUrl
    $sourcePath = if ($uri.IsFile) { $uri.LocalPath } else { $uri.AbsolutePath }
    $name = [System.IO.Path]::GetFileName($sourcePath)
    if ($name -match '[\\/]' -or $name.Contains([char]0)) {
        Write-Fail "manifest wheel_url basename contains path-separator chars: $name (from $WheelUrl)"
    }
    if (-not $name.EndsWith('.whl') -or ($name.Split('-').Count - 1) -lt 4) {
        Write-Fail "manifest wheel_url has non-PEP427 filename: $name (from $WheelUrl)"
    }
    return $name
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# `irm | iex` runs the script inline in the caller's PowerShell, so we cannot
# use `#Requires -RunAsAdministrator` (which errors confusingly before our own
# messages run) and cannot just re-exec ourselves from disk (the script does
# not live on disk). Instead: detect non-admin and re-launch via UAC with
# `Start-Process -Verb RunAs`, passing a command that re-fetches and pipes the
# same install URL, with the relevant MOBAIDE_* env vars reinstated.
function Assert-AdminOrElevate {
    if (Test-IsAdmin) { return }
    Write-Info "Not running elevated; relaunching under UAC to register the Windows service."
    # Discover claude.cmd here, BEFORE the UAC relaunch, so the lookup
    # runs under the originating user's env. For the standard `irm | iex`
    # consent flow the elevated session is the same user — env vars
    # carry through naturally — but if the operator supplies a different
    # admin account at the UAC prompt, that admin's profile may not
    # contain claude.cmd. Capturing under the originating user's env and
    # passing the absolute path through MOBAIDE_AGENT_CLAUDE_BIN_HINT
    # makes the discovery resilient to that cross-account elevation.
    $claudeBin = Find-ClaudeBinary
    if ($claudeBin) {
        $env:MOBAIDE_AGENT_CLAUDE_BIN_HINT = $claudeBin
    }
    $passthrough = @(
        'MOBAIDE_AGENT_PREFIX', 'MOBAIDE_AGENT_MANIFEST_URL',
        'MOBAIDE_AGENT_TRUST_KEY_B64', 'MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH',
        'MOBAIDE_AGENT_INSTALL_URL', 'MOBAIDE_AGENT_WINDOWS_SERVICE_URL',
        'MOBAIDE_AGENT_SKIP_INSTALL_QR', 'MOBAIDE_AGENT_SKIP_PYTHON_INSTALL',
        'MOBAIDE_AGENT_CLAUDE_BIN_HINT'
    )
    $envStmts = foreach ($name in $passthrough) {
        $val = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($val) {
            $escaped = $val -replace "'", "''"
            "`$env:$name = '$escaped'"
        }
    }
    $prefix = if ($envStmts) { ($envStmts -join '; ') + '; ' } else { '' }
    $urlEscaped = $Script:InstallUrl -replace "'", "''"
    $cmd = "$prefix" + "irm '$urlEscaped' | iex"
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-Command', $cmd)
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $psArgs -Verb RunAs -Wait
    } catch {
        Write-Fail "Installation cancelled at UAC prompt. Re-run when ready: irm $Script:InstallUrl | iex"
    }
    exit 0
}

function Find-Python {
    $candidates = @("py", "python", "python3")
    foreach ($cmd in $candidates) {
        $exe = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        # Skip the App Execution Alias stub under WindowsApps — it's a
        # reparse-point trampoline that opens the Microsoft Store and
        # never actually runs Python in an unattended `irm | iex` flow.
        if ($exe.Source -like "*\WindowsApps\*") { continue }
        try {
            $version = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
            if ($LASTEXITCODE -ne 0) { continue }
            $parts = $version.Trim().Split('.')
            $major = [int]$parts[0]; $minor = [int]$parts[1]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 12)) {
                Write-Info "Using $($exe.Source) ($version)"
                return $exe.Source
            }
        } catch {}
    }
    return $null
}

# Locate the user's claude.cmd at install time. Critical because the
# Windows service registers as LocalSystem (Install-Service line ~952
# calls pywin32's install verb with no obj= override), and LocalSystem's
# PATH is C:\Windows\system32;C:\Windows;... — it does NOT contain the
# installing user's %APPDATA%\npm, which is where `npm install -g
# @anthropic-ai/claude-code` lands claude.cmd. shutil.which("claude.cmd")
# inside the service therefore returns None and session creation 503s
# with claude_cli_missing=true. We capture the path now (while running
# under the user's env) and pin it into .env so the service can find it.
#
# Returns the absolute claude path as a string, or $null if not found
# (caller writes a warning and continues — the missing-CLI 503 then
# guides the operator at session-create time).
function Find-ClaudeBinary {
    foreach ($name in @('claude.cmd', 'claude.exe', 'claude')) {
        $hit = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $hit) { continue }
        # Skip the App Execution Alias trampoline under WindowsApps for
        # the same reason Find-Python does: it's a reparse-point that
        # never runs a real claude binary.
        if ($hit.Source -like "*\WindowsApps\*") { continue }
        return $hit.Source
    }
    # PATH miss: probe common npm install locations under the installing
    # user's profile. These cover default npm-global (%APPDATA%\npm),
    # relocated npm-global (%LOCALAPPDATA%\npm), machine-scope installs
    # (C:\ProgramData\npm), and the bundled-with-Node fallback.
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:APPDATA) {
        $candidates.Add((Join-Path $env:APPDATA 'npm\claude.cmd'))
        $candidates.Add((Join-Path $env:APPDATA 'npm\claude.exe'))
    }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'npm\claude.cmd'))
    }
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE 'AppData\Roaming\npm\claude.cmd'))
    }
    $candidates.Add('C:\ProgramData\npm\claude.cmd')
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'nodejs\claude.cmd'))
    }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            return (Resolve-Path -LiteralPath $c).ProviderPath
        }
    }
    return $null
}

# Both auto-install paths (winget and python.org) edit the persistent PATH
# but not the in-flight process environment. Recompose the in-session PATH
# from the Machine + User registry scopes so Find-Python can resolve the
# freshly-installed interpreter without a shell restart.
function Update-CurrentSessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Fast path: winget install Python.Python.3.12 at machine scope so the SCM
# service (running as LocalSystem by default) can later see the interpreter.
# Returns $true on success, $false on any failure (caller falls back to the
# python.org installer).
function Install-PythonViaWinget {
    Write-Info "Installing Python 3.12 via winget (machine scope, silent)..."
    # `| Out-Host` so winget's progress chatter prints to the operator but
    # doesn't pile onto the function's success pipeline. A `[bool]` return
    # mixed with an `[object[]]` of stdout lines would make
    # `if (Install-PythonViaWinget) { ... }` truthy even on failure (any
    # non-empty array is truthy in PowerShell, regardless of contents).
    & winget install --id Python.Python.3.12 --silent `
        --accept-package-agreements --accept-source-agreements `
        --scope machine --disable-interactivity | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "winget install Python.Python.3.12 failed (exit $LASTEXITCODE)."
        return $false
    }
    Update-CurrentSessionPath
    return $true
}

# Fallback path: download the official python.org installer and run it
# silently with per-machine scope. Used when winget is absent (Windows 10
# LTSC, Server <2025, Sandbox, fresh user profiles, GPO-locked Store) or
# when the winget invocation itself fails.
#
# Returns $true on success, $false otherwise. The pinned 3.12.x patch
# version is bumpable in source — python.org serves no stable "latest"
# alias, so every release has its own versioned URL.
function Install-PythonFromPythonOrg {
    $version = "3.12.10"
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $url = "https://www.python.org/ftp/python/$version/python-$version-$arch.exe"
    $tmp = Join-Path $env:TEMP ("python-installer-" + [guid]::NewGuid() + ".exe")
    Write-Info "Downloading Python $version ($arch) from python.org..."
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        try {
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 300
        } catch {
            Write-Warn "Failed to download Python installer from ${url}: $($_.Exception.Message)"
            return $false
        }
        # Defense-in-depth on top of TLS: verify the downloaded .exe is
        # Authenticode-signed by the Python Software Foundation before
        # running it as admin. Closes the rogue-CA / python.org-compromise
        # gap relative to the manifest/wheel path (which already verifies
        # Ed25519 + SHA-256). Authenticode roots in Microsoft's program,
        # independent of the host's TLS trust store.
        Write-Info "Verifying Authenticode signature on the downloaded installer..."
        $sig = Get-AuthenticodeSignature -FilePath $tmp
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike '*Python Software Foundation*') {
            Write-Warn "python.org installer failed Authenticode verification (status=$($sig.Status), subject=$($sig.SignerCertificate.Subject))."
            return $false
        }
        Write-Info "Running Python $version installer (silent, machine scope)..."
        # Machine-scope (InstallAllUsers=1) matches the prior winget
        # --scope machine choice so the SCM service running as LocalSystem
        # sees the interpreter. SimpleInstall=1 suppresses the optional-
        # features page; Include_test=0 trims the install footprint.
        try {
            $proc = Start-Process -FilePath $tmp -Wait -PassThru -ArgumentList `
                '/quiet', 'InstallAllUsers=1', 'PrependPath=1', `
                'Include_test=0', 'Include_launcher=1', 'SimpleInstall=1'
        } catch {
            Write-Warn "Python installer process failed to start: $($_.Exception.Message)"
            return $false
        }
        # 0 = success; 3010 = success but reboot pending (the rest of the
        # install still works); any other code is a real failure.
        if ($proc.ExitCode -eq 3010) {
            Write-Warn "Python installer reported reboot pending (exit 3010); continuing."
        } elseif ($proc.ExitCode -ne 0) {
            Write-Warn "Python installer exited with code $($proc.ExitCode)."
            return $false
        }
        Update-CurrentSessionPath
        return $true
    } finally {
        $ProgressPreference = $oldProgress
        Remove-Item -Force $tmp -ErrorAction SilentlyContinue
    }
}

# Two-stage fallback chain:
#   1. winget (fast path, present on modern Windows 11 / 10).
#   2. official python.org installer (works on every Windows SKU,
#      including LTSC / Server / Sandbox / GPO-locked hosts where winget
#      is absent).
# Hard-fails only when both stages fail.
function Install-Python {
    $wingetAvailable = [bool](Get-Command winget -ErrorAction SilentlyContinue)
    if ($wingetAvailable) {
        if (Install-PythonViaWinget) { return }
        Write-Warn "Falling back to python.org installer."
    } else {
        Write-Info "winget is not available on PATH; using python.org installer."
    }
    if (Install-PythonFromPythonOrg) { return }
    Write-Fail @"
Python 3.12+ is required but could not be auto-installed (both the winget
path and the python.org fallback failed). Install Python 3.12 manually from
https://www.python.org/downloads/release/python-3120/ (choose
'Add python.exe to PATH' and 'Install for all users'), then re-run:
    irm $Script:InstallUrl | iex
Or set MOBAIDE_AGENT_SKIP_PYTHON_INSTALL=1 to require a pre-installed
interpreter (CI / locked-down hosts).
"@
}

function Resolve-Python {
    $py = Find-Python
    if ($py) { return $py }
    if ($Script:SkipPythonInstall) {
        Write-Fail "Python 3.12+ is required (MOBAIDE_AGENT_SKIP_PYTHON_INSTALL is set, so the installer will not auto-install it)."
    }
    Install-Python
    $py = Find-Python
    if (-not $py) {
        Write-Fail "Python 3.12+ still not found after auto-install. Try re-running, or install manually from https://www.python.org/downloads/."
    }
    return $py
}

# Trust-key validation and manifest-signature verification (Confirm-TrustKey,
# Test-ManifestSignature below) import `cryptography` — a PyPI dep that is NOT
# in stdlib and is NOT present on a freshly-installed python.org / winget
# interpreter. The wheel installed later in Install-Release pulls cryptography
# transitively, but those verification steps happen BEFORE the wheel is
# fetched, so we'd otherwise hard-fail with ModuleNotFoundError on every host
# without a pre-existing cryptography install. Fast inline `-c` probe — no
# embedded quotes / f-strings, so it doesn't trip the
# test_install_ps1_does_not_pass_heredoc_to_python_dash_c regex pin.
function Test-CryptographyImportable {
    param([Parameter(Mandatory=$true)] $PythonExe)
    try {
        # `2>$null` silences stderr; `| Out-Null` silences stdout. The
        # function returns a `[bool]`; if anything (sitecustomize.py,
        # warning bridge, future Python build) printed to stdout, the
        # return would become an `[object[]]` and every
        # `if (Test-CryptographyImportable ...) { ... }` call site would
        # be truthy on failure (any non-empty array is truthy).
        & $PythonExe -c "import cryptography" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Ephemeral verification venv (Poetry installer pattern). `cryptography`
# ships prebuilt wheels for win_amd64 / win_arm64 on PyPI, so this is a
# fast pip install + a one-shot trust-key / manifest verification, then
# the caller's finally tears the directory down. Deliberately NOT
# `pip install --upgrade pip` first: the wheel-installing Install-Release
# venv does that (line ~470), but this venv runs exactly one `pip install`
# and is then deleted — the upgrade is a wasted network round-trip here.
function New-BootstrapVenv {
    param([Parameter(Mandatory=$true)] $PythonExe)
    $root = Join-Path $env:TEMP ("mobaide-verifier-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    try {
        Write-Info "Bootstrapping verification venv (cryptography>=43.0)..."
        # `| Out-Host` for the same reason as Install-Release: this
        # function returns a hashtable, and native stdout on the pipeline
        # would corrupt `$verifier.Python` / `$verifier.Root` downstream.
        & $PythonExe -m venv $root | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Fail @"
Could not create a verification venv with $PythonExe (python -m venv exited
$LASTEXITCODE). The base interpreter is missing or broken `ensurepip`/`venv`.
Reinstall Python 3.12 from https://www.python.org/downloads/release/python-3120/
or set MOBAIDE_AGENT_SKIP_PYTHON_INSTALL=1 and provide a healthier interpreter
on PATH.
"@
        }
        $venvPython = Join-Path $root "Scripts\python.exe"
        $venvPip = Join-Path $root "Scripts\pip.exe"
        # Lock down pip's runtime configuration so a hostile-configured host
        # cannot redirect `cryptography` to an attacker-controlled mirror.
        # A compromised cryptography wheel could forge a passing manifest
        # signature verify — the very check this venv exists for — so the
        # bootstrap install must be its own root of trust on top of TLS.
        #
        # Two layers, both required:
        #   * `--index-url https://pypi.org/simple` — declares the canonical
        #     primary index explicitly.
        #   * `--isolated` — tells pip to ignore environment variables
        #     (PIP_EXTRA_INDEX_URL, PIP_FIND_LINKS, PIP_TRUSTED_HOST,
        #     PIP_INDEX_URL, ...) AND user/system config files
        #     (~/.pip/pip.conf, %APPDATA%\pip\pip.ini, /etc/pip.conf).
        #     Without --isolated, --index-url alone leaves PIP_EXTRA_INDEX_URL
        #     and PIP_FIND_LINKS in play — pip treats those as ADDITIVE
        #     sources with no priority, so an attacker publishes
        #     `cryptography==999.0.0` on their mirror and pip prefers it
        #     by version (classic dependency-confusion vector,
        #     CVE-2018-20225-class).
        #
        # The agent's release wheel goes through the manifest's wheel_url
        # rather than PyPI, so this lockdown matches the trust posture of
        # the rest of the install. Operators behind an air-gapped /
        # self-hosted PyPI mirror can pre-install `cryptography>=43.0` on
        # the system Python (fast path), bypassing this slow path entirely.
        & $venvPip install --isolated --no-cache-dir --disable-pip-version-check --quiet --index-url https://pypi.org/simple "cryptography>=43.0" | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Fail @"
Could not install the `cryptography` package required to verify the manifest
signature. Most likely causes:
  * No network access to PyPI (pypi.org).
  * A corporate proxy blocking pypi.org — set HTTPS_PROXY / HTTP_PROXY.
  * No prebuilt wheel for this Python/arch (check
    https://pypi.org/project/cryptography for the matching wheel).
Workaround: pre-install on the system Python (`pip install 'cryptography>=43.0'`)
and re-run.
"@
        }
        return @{ Python = $venvPython; Root = $root }
    } catch {
        Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
        throw
    }
}

# Return a Python that can import `cryptography`. Fast path: the bare
# interpreter already has it (most pre-existing dev installs, system-managed
# Python on hosts where another package pulled cryptography). Slow path: build
# an ephemeral bootstrap venv. The hashtable-with-null-Root shape lets the
# Main finally block clean up both paths uniformly.
function Resolve-VerifierPython {
    param([Parameter(Mandatory=$true)] $PythonExe)
    if (Test-CryptographyImportable -PythonExe $PythonExe) {
        Write-Info "cryptography already importable on $PythonExe; skipping bootstrap venv."
        return @{ Python = $PythonExe; Root = $null }
    }
    return New-BootstrapVenv -PythonExe $PythonExe
}

function Get-Manifest {
    param($PythonExe, $ManifestPath)
    Write-Info "Fetching manifest from $Script:ManifestUrl"
    Invoke-WebRequest -Uri $Script:ManifestUrl -OutFile $ManifestPath -UseBasicParsing
}

# Round-trip validate that $Script:TrustKeyB64 decodes to a 32-byte Ed25519
# public key. Used by both the env-var-pinned path and the
# central-server-fetched path so a malformed key surfaces immediately at
# install time as "your key is malformed" rather than later as a confusing
# "manifest signature does not verify".
function Confirm-TrustKey {
    param($PythonExe)
    $validateScript = @'
import base64, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

key_b64 = sys.argv[1]
try:
    Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
except Exception as exc:
    sys.exit(f"trust key is not a valid base64 Ed25519 public key: {exc}")
'@
    Invoke-PythonScript -PythonExe $PythonExe -Script $validateScript -Arguments @($Script:TrustKeyB64)
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Trust key validation failed."
    }
}

# Trust-key bootstrap: ensure $Script:TrustKeyB64 is set before fetching the
# manifest. If the operator pinned via MOBAIDE_AGENT_TRUST_KEY_B64, validate
# and use it. Otherwise fetch the manifest-signing public key from the
# central_server endpoint (host derived from $Script:ManifestUrl).
# Fail-closed if neither path yields a key — never silently ship an install
# whose auto-updater can't verify rollouts.
#
# Two security guards on the fetch path:
# * User-info in $Script:ManifestUrl is rejected (.NET's [System.Uri] strips
#   it from .Authority, so we'd silently retarget — explicitly check
#   .UserInfo before building the origin).
# * http:// schemes refused unless
#   MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1 is set.
function Initialize-TrustKey {
    param($PythonExe)
    if ($Script:TrustKeyB64) {
        Write-Info "Using operator-pinned trust key (MOBAIDE_AGENT_TRUST_KEY_B64)."
        Confirm-TrustKey -PythonExe $PythonExe
        return
    }
    $manifestUri = [System.Uri]$Script:ManifestUrl
    if ($manifestUri.UserInfo) {
        Write-Fail "MOBAIDE_AGENT_MANIFEST_URL must not contain user-info (user@/user:pass@): $Script:ManifestUrl"
    }
    $scheme = $manifestUri.Scheme.ToLowerInvariant()
    if ($scheme -ne "http" -and $scheme -ne "https") {
        Write-Fail "MOBAIDE_AGENT_MANIFEST_URL has unsupported scheme '$scheme': $Script:ManifestUrl"
    }
    if ($scheme -eq "http" -and -not $Script:AllowInsecureTrustKeyFetch) {
        Write-Fail @"
Trust-key fetch over cleartext http:// is refused by default
(MOBAIDE_AGENT_MANIFEST_URL=$Script:ManifestUrl). Set
MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1 to override (only safe on
a trusted local network, e.g. self-host loopback).
"@
    }
    $origin = "$($manifestUri.Scheme)://$($manifestUri.Authority)"
    $trustUrl = "$origin/api/v1/agent/trust_key"
    Write-Info "Fetching trust key from $trustUrl"
    try {
        $response = Invoke-WebRequest -Uri $trustUrl -UseBasicParsing -TimeoutSec 30
    } catch {
        Write-Fail @"
Could not bootstrap trust key from ${trustUrl}: $($_.Exception.Message)
Set MOBAIDE_AGENT_TRUST_KEY_B64=<base64-ed25519> explicitly,
or ensure central_server has MANIFEST_SIGNING_PUBLIC_KEY configured.
See docs/versioning-and-updates.md -> Trust-key bootstrap.
"@
    }
    try {
        $payload = $response.Content | ConvertFrom-Json
    } catch {
        Write-Fail "Trust-key endpoint returned non-JSON: $($_.Exception.Message)"
    }
    $key = ($payload.public_key_b64 | Out-String).Trim()
    if (-not $key) {
        Write-Fail "Trust-key endpoint returned an empty public_key_b64."
    }
    $Script:TrustKeyB64 = $key
    # Round-trip validate fail-closed BEFORE any files land on disk so a
    # misconfigured central_server can't smuggle in a malformed key.
    Confirm-TrustKey -PythonExe $PythonExe
    Write-Info "Trust key bootstrapped from central_server."
}

function Test-ManifestSignature {
    param($PythonExe, $ManifestPath)
    if (-not $Script:TrustKeyB64) {
        # Initialize-TrustKey should have populated this; defensive only.
        Write-Fail "Internal error: trust key not bootstrapped before signature verification."
    }
    Write-Info "Verifying manifest signature"
    $verifyScript = @'
import base64, json, sys
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

manifest_path, public_key_b64 = sys.argv[1:3]
record = json.loads(Path(manifest_path).read_text())
body, sig = record["body"], record["signature_b64"]
canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
try:
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64)).verify(
        base64.b64decode(sig), canon
    )
except InvalidSignature:
    sys.exit("manifest signature does not verify")
'@
    Invoke-PythonScript -PythonExe $PythonExe -Script $verifyScript -Arguments @($ManifestPath, $Script:TrustKeyB64)
    if ($LASTEXITCODE -ne 0) { Write-Fail "Manifest signature verification failed" }
}

function Install-Release {
    param($PythonExe, $ManifestPath)
    # Parse the manifest in PowerShell so the staging step does not depend
    # on Python's network stack at all — see Invoke-AgentDownload's header
    # for the SSL trust-store rationale. Three values come out: the version
    # (semver, used to name $releaseDir and tag the User-Agent), and the
    # two asset URLs that PowerShell will download.
    $manifestJson = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
    $version = "$($manifestJson.body.version)".Trim()
    if (-not $version) { Write-Fail "Manifest is missing 'version'" }
    $tarballUrl = "$($manifestJson.body.tarball_url)".Trim()
    $wheelUrl   = "$($manifestJson.body.wheel_url)".Trim()
    if (-not $tarballUrl) { Write-Fail "Manifest is missing 'tarball_url'" }
    if (-not $wheelUrl)   { Write-Fail "Manifest is missing 'wheel_url'" }
    $wheelBasename = Get-WheelBasename -WheelUrl $wheelUrl

    $releaseDir = Join-Path (Join-Path $Script:Prefix "releases") $version

    Write-Info "Staging release $version into $releaseDir"
    if (Test-Path $releaseDir) { Remove-Item -Recurse -Force $releaseDir }
    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null

    $tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("mobaide-agent-" + [guid]::NewGuid()))
    try {
        $tarballPath = Join-Path $tmp.FullName "agent.tar.gz"
        # Stage the wheel under its canonical PEP 427 filename directly so
        # pip 25.2+'s wheel-filename validator accepts it at install time.
        $wheelPath = Join-Path $tmp.FullName $wheelBasename
        # Identify as the installer so a Cloudflare-fronted wheel host does
        # not classify the request as the default PowerShell UA (Bot Fight
        # Mode 1010). Mirrors deploy/install.sh's USER_AGENT header on its
        # own urllib requests.
        $userAgent = "mobaide-agent-installer/$version"
        Invoke-AgentDownload -Url $tarballUrl -OutFile $tarballPath -UserAgent $userAgent
        Invoke-AgentDownload -Url $wheelUrl   -OutFile $wheelPath   -UserAgent $userAgent

        # The script prints exactly one line — the staged wheel's full path.
        # All network I/O happened in PowerShell above; this script only
        # verifies sha256 against the manifest and extracts the tarball.
        $stageScript = @'
import hashlib, json, sys, tarfile
from pathlib import Path

manifest_path, tarball_path, wheel_path, release_dir = sys.argv[1:5]
record = json.loads(Path(manifest_path).read_text())
body = record["body"]

def verify_sha256(path, expected):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        sys.exit(f"sha256 mismatch for {path}")

verify_sha256(tarball_path, body["tarball_sha256"])
verify_sha256(wheel_path, body["wheel_sha256"])

with tarfile.open(tarball_path, mode="r:gz") as tar:
    tar.extractall(release_dir, filter="data")
print(wheel_path)
'@
        $wheel = Invoke-PythonScript -PythonExe $PythonExe -Script $stageScript -Arguments @($ManifestPath, $tarballPath, $wheelPath, $releaseDir)
        if ($LASTEXITCODE -ne 0) { Write-Fail "Staging failed" }
        $wheel = ($wheel | Out-String).Trim()

        $venv = Join-Path $releaseDir "venv"
        Write-Info "Creating venv in $venv"
        # Every `&`-invoked native command emits its stdout onto the
        # surrounding function's success pipeline. Without `| Out-Host`,
        # pip's `[notice]` lines and `python -m venv`'s chatter get glued
        # onto Install-Release's final `return $version`, and `Main`
        # receives an `object[]` instead of a string. Downstream
        # `Join-Path -ChildPath $Version` then crashes with
        # `Cannot convert 'System.Object[]' to ... 'ChildPath'`.
        & $PythonExe -m venv $venv | Out-Host
        $venvPython = Join-Path $venv "Scripts\python.exe"
        $venvPip = Join-Path $venv "Scripts\pip.exe"
        # pip on Windows refuses to self-overwrite through the `pip.exe`
        # console_scripts shim — that shim is the running process and is
        # locked. Upgrade through the interpreter so the shim isn't held
        # open during replacement (pypa/pip#6784).
        & $venvPython -m pip install --no-cache-dir --upgrade pip | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "pip self-upgrade failed (exit $LASTEXITCODE); continuing with bundled pip."
        }
        & $venvPip install --no-cache-dir $wheel | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Fail "Wheel install failed" }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }

    $currentTmp = Join-Path $Script:Prefix "current_version.tmp"
    $current = Join-Path $Script:Prefix "current_version"
    Set-Content -Path $currentTmp -Value $version -NoNewline
    Move-Item -Force $currentTmp $current
    Write-Info "Pinned current_version=$version"
    return $version
}

function Write-TrustKey {
    # Always called after Initialize-TrustKey, so $Script:TrustKeyB64 is
    # always set. The agent's auto-updater requires this file at runtime
    # (mobaide.agent.install_paths.read_trust_public_key).
    if (-not $Script:TrustKeyB64) {
        Write-Fail "Internal error: Write-TrustKey called without a bootstrapped key."
    }
    $trustDir = Join-Path $Script:Prefix "trust"
    New-Item -ItemType Directory -Path $trustDir -Force | Out-Null
    Set-Content -Path (Join-Path $trustDir "central_server.pub") -Value $Script:TrustKeyB64 -NoNewline
}

function Write-AgentEnv {
    param($Version)
    # Idempotent: re-running the installer never clobbers a paired agent's
    # credentials (no --force is passed). Auto-generates JWT_SECRET_KEY and
    # prefills RELAY_SERVER_URL on first install.
    $releaseDir = Join-Path (Join-Path $Script:Prefix "releases") $Version
    $releasePython = Join-Path $releaseDir "venv\Scripts\python.exe"

    # Prefer the pre-UAC hint captured under the originating user's env
    # (see Assert-AdminOrElevate). Falls back to a fresh probe inside
    # the elevated session, which for the canonical `irm | iex` consent
    # flow runs as the same user. Falls back to $null if neither path
    # discovers claude.cmd — we still complete the install (warn only)
    # so the agent boots and the operator can fix the missing CLI
    # post-install per "warn-and-continue" policy.
    $claudeBin = $env:MOBAIDE_AGENT_CLAUDE_BIN_HINT
    if (-not $claudeBin) { $claudeBin = Find-ClaudeBinary }
    if ($claudeBin) {
        Write-Info "Detected Claude CLI at $claudeBin"
    } else {
        Write-Warn @"
Could not auto-detect the Claude Code CLI (claude.cmd). Install it with:
    npm install -g @anthropic-ai/claude-code
Then either re-run this installer, or set MOBAIDE_CLAUDE_BIN to the
absolute path in $Script:Prefix\mobaide_agent\.env and restart:
    Restart-Service $Script:ServiceName
"@
    }

    $envFile = Join-Path (Join-Path $Script:Prefix "mobaide_agent") ".env"
    $envExists = Test-Path -LiteralPath $envFile

    if (-not $envExists) {
        Write-Info "Writing prepared agent .env via init-env"
        $extraArgs = @()
        if ($claudeBin) { $extraArgs += @('--claude-bin', $claudeBin) }
        & $releasePython -m mobaide.agent.config init-env --prefix $Script:Prefix @extraArgs
        if ($LASTEXITCODE -ne 0) { Write-Fail "init-env failed (exit $LASTEXITCODE)" }
        return
    }

    # Existing .env (re-install over a paired agent): leave credentials
    # alone. Surgically backfill MOBAIDE_CLAUDE_BIN only if it's not
    # already set — an operator's manual edit always wins. This
    # implements the "Surgical set-env only" policy.
    Write-Info "Existing $envFile preserved (re-install)."
    if (-not $claudeBin) { return }
    $existingClaudeBin = Read-EnvValue -Path $envFile -Key 'MOBAIDE_CLAUDE_BIN'
    if ($existingClaudeBin) {
        Write-Info "MOBAIDE_CLAUDE_BIN already set in $envFile; leaving as-is."
        return
    }
    Write-Info "Backfilling MOBAIDE_CLAUDE_BIN=$claudeBin into $envFile"
    & $releasePython -m mobaide.agent.config set-env --prefix $Script:Prefix `
        --key MOBAIDE_CLAUDE_BIN --value $claudeBin
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "set-env failed (exit $LASTEXITCODE); edit $envFile manually and restart $Script:ServiceName."
    }
}

# Parse a KEY=VALUE line from <prefix>/mobaide_agent/.env. Single
# lookup, no global caching: this helper is called twice per install
# (port for /api/health, future fields if added) and the file is small.
function Read-EnvValue {
    param(
        [Parameter(Mandatory=$true)] [string] $Path,
        [Parameter(Mandatory=$true)] [string] $Key,
        [string] $Default = ""
    )
    if (-not (Test-Path -LiteralPath $Path)) { return $Default }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        # Match `KEY=VALUE` with optional surrounding whitespace; ignore
        # comments and blank lines. The env-writer renders one key per
        # line, no quoting (init-env at mobaide.agent.config), so a
        # naive split on the first `=` is sufficient and matches what
        # pydantic-settings consumes at agent boot.
        if ($line -match "^\s*$Key\s*=\s*(.*)\s*$") {
            return $matches[1]
        }
    }
    return $Default
}

# Block until the agent's FastAPI /api/health endpoint responds 200,
# or hard-fail with an Event-Viewer diagnostic. The Windows-service
# bug this guards against (the user's original report): `Start-Service`
# returns success because SCM accepted the start request, but the
# Python service host can still crash inside SvcDoRun before the
# launcher subprocess opens the HTTP socket — and the operator only
# discovers the failure when the QR they scan never pairs. The 60s
# budget covers a cold-start that includes SCM init + svc-venv import
# + uvicorn bind on slow hosts; failing fast with the Event-Viewer
# pointer is the difference between "obvious install bug" and "QR
# silently doesn't work".
function Wait-AgentHealthy {
    param(
        [Parameter(Mandatory=$true)] [string] $Prefix,
        [int] $TimeoutSec = 60
    )
    $envFile = Join-Path (Join-Path $Prefix "mobaide_agent") ".env"
    # ``port`` and ``host`` default to AgentConfig's documented defaults
    # (mobaide/agent/config.py:43-44): 0.0.0.0:8000. The installer always
    # hits 127.0.0.1 (HOST is the bind address, not the contact address).
    $port = Read-EnvValue -Path $envFile -Key 'PORT' -Default '8000'
    $url = "http://127.0.0.1:$port/api/health"
    Write-Info "Waiting for agent /api/health at $url (timeout ${TimeoutSec}s)"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        while ((Get-Date) -lt $deadline) {
            try {
                $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
                if ($resp.StatusCode -eq 200) {
                    return ($resp.Content | ConvertFrom-Json)
                }
            } catch {
                # Connection refused / timeout / 5xx — keep polling.
            }
            Start-Sleep -Milliseconds 500
        }
    } finally {
        $ProgressPreference = $oldProgress
    }
    Write-Fail @"
Agent service registered but /api/health did not respond within ${TimeoutSec}s.
The service may have crashed at startup. Inspect:
    eventvwr.msc -> Windows Logs -> Application (Source: MobaideAgent)
Or from an elevated PowerShell:
    Get-EventLog -LogName Application -Source MobaideAgent -Newest 20
    Get-Service MobaideAgent
    Get-Content "$Prefix\logs\agent.err.log" -Tail 50
    Get-Content "$Prefix\logs\agent.out.log" -Tail 100
"@
}

function Show-PairingQrFromService {
    param(
        [Parameter(Mandatory=$true)] [string] $Prefix,
        [Parameter(Mandatory=$true)] [string] $Version,
        $Health,
        [int] $PendingTimeoutSec = 60
    )
    # Documented opt-out for unattended/CI provisioning. Identical
    # contract to deploy/install.sh's MOBAIDE_AGENT_SKIP_INSTALL_QR.
    if ($env:MOBAIDE_AGENT_SKIP_INSTALL_QR) {
        Write-Info "MOBAIDE_AGENT_SKIP_INSTALL_QR set; not rendering pairing QR."
        return
    }
    # Re-install on an already-paired agent: /api/health.pairing_pending
    # is $false because credentials are already in .env. No QR needed;
    # avoids a stale "scan me" prompt on an idempotent re-run.
    if ($Health -and $Health.pairing_pending -eq $false) {
        Write-Info "Agent reports pairing_pending=false; already paired. Skipping QR."
        return
    }
    # Lifespan creates the pairing-monitor task concurrently with the
    # yield, so /api/health responds 200 BEFORE the monitor has called
    # /api/pairing/initiate and saved the pending file. Poll for the
    # file to appear; if it never does within the budget, the service
    # is up but can't reach central_server (most likely network /
    # firewall) — surface a directed error rather than rendering a
    # nonexistent QR.
    $pendingFile = Join-Path (Join-Path $Prefix "run") "pending_pairing.json"
    Write-Info "Waiting for pairing nonce at $pendingFile (timeout ${PendingTimeoutSec}s)"
    $deadline = (Get-Date).AddSeconds($PendingTimeoutSec)
    while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $pendingFile)) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-Path -LiteralPath $pendingFile)) {
        Write-Fail @"
Agent service is up but did not initiate pairing within ${PendingTimeoutSec}s
(no $pendingFile). The service likely cannot reach central_server. Check:
    Test-NetConnection -ComputerName api.mobaide.com -Port 443
    Get-EventLog -LogName Application -Source MobaideAgent -Newest 20
The service is still running; once connectivity is restored it will
initiate a nonce on its own. Run `mobaide-agent show-qr` to re-display.
"@
    }
    $releaseDir = Join-Path (Join-Path $Prefix "releases") $Version
    $releaseScripts = Join-Path $releaseDir "venv\Scripts"
    $mobaideAgent = Join-Path $releaseScripts "mobaide-agent.exe"
    if (-not (Test-Path -LiteralPath $mobaideAgent)) {
        $mobaideAgent = Join-Path $releaseScripts "mobaide-agent"
    }
    Write-Info "Rendering pairing QR (scan from the Mobaide mobile app)"
    # ``--require-pending`` keeps this call from initiating a second
    # nonce (the running service has already initiated): two concurrent
    # /ws/pairing/{nonce} dials for the same nonce would race, and the
    # central server rejects the loser with WS close 4001. The flag
    # makes the CLI render-from-file only; on exit 7 (file vanished
    # between our Test-Path and the CLI's load) we hard-fail rather
    # than fall through to a fresh initiate.
    & $mobaideAgent show-qr --require-pending --prefix $Prefix
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "mobaide-agent show-qr --require-pending failed (exit $LASTEXITCODE). Run `mobaide-agent show-qr` from a new PowerShell window."
    }
}

function Install-Service {
    param($PythonExe)
    # Architectural note: a Windows service binary MUST implement the
    # SCM ABI (StartServiceCtrlDispatcher / SetServiceStatus(RUNNING)
    # within ~30s, SERVICE_CONTROL_* handlers). The pre-fix design
    # registered `cmd.exe /c launcher.cmd` — cmd.exe is a console
    # interpreter and does none of those, so SCM timed out with
    # "Cannot start service MobaideAgent on computer '.'" (error 1053).
    #
    # The fix: a real service hosted in a dedicated, version-independent
    # venv at <prefix>/svc/venv/, running mobaide_service.py (a pywin32
    # ServiceFramework subclass). The service module reads current_version
    # at each start and spawns the active release's
    # `python -m mobaide.launcher` as a subprocess; the launcher then
    # does crash-loop detection + rollback exactly as before.
    $svcDir = Join-Path $Script:Prefix "svc"
    $svcVenv = Join-Path $svcDir "venv"
    $svcVenvPython = Join-Path $svcVenv "Scripts\python.exe"
    $svcModule = Join-Path $svcDir "mobaide_service.py"

    New-Item -ItemType Directory -Path $svcDir -Force | Out-Null

    if (-not (Test-Path $svcVenvPython)) {
        Write-Info "Creating service host venv at $svcVenv"
        # `| Out-Host` so `python -m venv`'s chatter does not pile onto
        # this function's success pipeline. Same precedent as
        # Install-Release / New-BootstrapVenv (both pin this pattern in
        # tests/test_install_ps1.py).
        & $PythonExe -m venv $svcVenv | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Failed to create service host venv at $svcVenv (python -m venv exit $LASTEXITCODE)"
        }
    } else {
        Write-Info "Reusing existing service host venv at $svcVenv"
    }

    # `pip install pywin32` into the svc venv. --isolated mirrors the
    # New-BootstrapVenv hardening: closes PIP_EXTRA_INDEX_URL /
    # PIP_FIND_LINKS dependency-confusion (CVE-2018-20225-class) and
    # ignores user/system pip.ini that could otherwise redirect pywin32
    # to a hostile mirror.
    Write-Info "Installing pywin32 in service host venv"
    & $svcVenvPython -m pip install --isolated --no-cache-dir --disable-pip-version-check --index-url https://pypi.org/simple pywin32 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to install pywin32 in service host venv (pip exit $LASTEXITCODE)"
    }

    # Fetch the service module from the same distribution channel as
    # install.ps1 itself (gh-pages → get.mobaide.com). The publish-gh-pages
    # workflow ships deploy/install.ps1 and deploy/windows_service.py in
    # the same push, so the two cannot desync. The pre-fix design copied
    # the file out of the installed wheel, which coupled installer changes
    # to wheel re-tagging and produced
    # "Service module source not found at ...\releases\<v>\venv\..." when
    # the installer was newer than the wheel. The svc venv only needs
    # pywin32 + stdlib, so we don't install the whole mobaide package
    # here.
    Write-Info "Downloading service module from $Script:WindowsServiceUrl"
    # Versionless UA: deliberate divergence from Install-Release's
    # `mobaide-agent-installer/$version` form. The version token there
    # exists to pass Cloudflare Bot Fight Mode on the CDN-fronted wheel
    # host (see Invoke-AgentDownload header). gh-pages is not Cloudflare-
    # fronted in the same way, and $Version is no longer in scope after
    # the Install-Service `-Version` param removal, so the bare string
    # is sufficient.
    Invoke-AgentDownload -Url $Script:WindowsServiceUrl -OutFile $svcModule -UserAgent "mobaide-agent-installer"
    if (-not (Test-Path $svcModule)) {
        Write-Fail "Service module download failed: $svcModule does not exist after fetch from $Script:WindowsServiceUrl"
    }
    Write-Info "Wrote service module to $svcModule"

    # If the service already exists, stop it before re-registering so
    # the running python.exe doesn't hold the svc-venv files locked.
    # Removing-and-re-installing is the safe path when the binPath may
    # have changed (e.g. upgrading from the pre-fix cmd.exe binPath).
    $existing = Get-Service -Name $Script:ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Info "Stopping existing $Script:ServiceName service before re-registering"
        try {
            Stop-Service -Name $Script:ServiceName -Force -ErrorAction Stop
        } catch {
            Write-Warn "Could not stop existing service (continuing): $($_.Exception.Message)"
        }
        # `python mobaide_service.py remove` calls
        # win32serviceutil.RemoveService — idempotent if SCM already
        # cleared the entry.
        & $svcVenvPython $svcModule remove | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Existing $Script:ServiceName remove returned exit $LASTEXITCODE; continuing."
        }
    }

    Write-Info "Registering $Script:ServiceName service via pywin32"
    # The module's `install` verb calls win32serviceutil.InstallService
    # with exeName=sys.executable (the svc venv's python.exe) and
    # exeArgs="-u <this_file>". This sidesteps pythonservice.exe's
    # python<N>.dll search problem inside a venv (pywin32 #1450, #2032)
    # and produces a service binary path of
    # `<svc venv>\Scripts\python.exe -u <prefix>\svc\mobaide_service.py`.
    & $svcVenvPython $svcModule install | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to register $Script:ServiceName service (pywin32 install exit $LASTEXITCODE)"
    }
    # Defence-in-depth on the silent-failure anti-pattern that hid the
    # pre-fix bug: re-check SCM after the install verb and fail with a
    # message that names the real problem, instead of letting the next
    # cmdlet emit its own confusing "Cannot find any service" error.
    if (-not (Get-Service -Name $Script:ServiceName -ErrorAction SilentlyContinue)) {
        Write-Fail "Service $Script:ServiceName did not appear in SCM after pywin32 install. Re-run as Administrator and check the Windows Event Log."
    }

    # Three SCM-driven restarts at 5s spacing — the launcher rolls back
    # on its third boot attempt within the same window. reset=600 keeps
    # the failure counter alive long enough for the rolled-back release
    # to prove stable before SCM forgets the prior crash burst.
    # Best-effort: a recovery-policy misconfig is a warning, not fatal —
    # the launcher's own crash-loop rollback (mobaide.launcher) still
    # works without SCM-side restarts.
    & sc.exe failure $Script:ServiceName reset= 600 actions= restart/5000/restart/5000/restart/5000 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Could not configure failure recovery for $Script:ServiceName (sc.exe failure exit $LASTEXITCODE); continuing."
    }
    Start-Service -Name $Script:ServiceName
}

function Write-Completion {
    param($Version)
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  Mobaide agent installed"
    Write-Host "  Prefix:  $Script:Prefix"
    Write-Host "  Version: $Version"
    Write-Host "  Service: $Script:ServiceName (Running)"
    Write-Host ""
    Write-Host "  Re-render QR:  mobaide-agent show-qr"
    Write-Host "  Re-pair:       mobaide-agent re-pair"
    Write-Host "  Restart:       Restart-Service $Script:ServiceName"
    Write-Host "============================================"
}

function Main {
    Assert-AdminOrElevate
    Write-Info "Installing mobaide_agent into $Script:Prefix"
    New-Item -ItemType Directory -Path $Script:Prefix -Force | Out-Null

    $python = Resolve-Python
    # Verifier Python carries `cryptography` for trust-key + manifest signature
    # checks. Fast path returns $python unchanged ($verifier.Root = $null); slow
    # path returns an ephemeral bootstrap venv that the outer finally tears
    # down. $python (bare interpreter) still drives Install-Release's release
    # venv creation — `python -m venv` from inside another venv would create a
    # nested venv pointing at the bootstrap's interpreter, which would
    # disappear when the bootstrap is cleaned up.
    $verifier = Resolve-VerifierPython -PythonExe $python
    $version = $null
    try {
        # Order: bootstrap key (env var or central_server fetch) -> pin to disk
        # so the agent picks it up at runtime -> fetch manifest -> verify it
        # against the same key -> stage. Initialize-TrustKey fails the install
        # with an actionable message if neither path yields a key.
        Initialize-TrustKey -PythonExe $verifier.Python
        Write-TrustKey

        $manifest = New-TemporaryFile
        try {
            Get-Manifest -PythonExe $python -ManifestPath $manifest
            Test-ManifestSignature -PythonExe $verifier.Python -ManifestPath $manifest
            $version = Install-Release -PythonExe $python -ManifestPath $manifest
        } finally {
            Remove-Item $manifest -ErrorAction SilentlyContinue
        }
    } finally {
        if ($verifier.Root) {
            Remove-Item -Recurse -Force $verifier.Root -ErrorAction SilentlyContinue
        }
    }

    Write-AgentEnv -Version $version

    # New ordering (the fix for the original "service doesn't start +
    # QR shows but pairing doesn't land" report):
    #   1. Install + Start-Service        — bring the agent service up.
    #   2. Wait-AgentHealthy              — poll /api/health until 200,
    #                                       so we KNOW the service is
    #                                       fully booted (not just that
    #                                       SCM accepted the start).
    #   3. Show-PairingQrFromService      — wait for the service's
    #                                       pairing-monitor to write
    #                                       pending_pairing.json, then
    #                                       render the QR from it.
    # The previous order rendered the QR before the service was up,
    # so a scan during the gap had nothing to dispatch the claim to
    # and RELAY_AGENT_ID / RELAY_AGENT_TOKEN stayed empty. Inverting
    # makes the running service the single initiator of the nonce
    # (no two-process race) and surfaces a clear diagnostic when the
    # service fails to start (Wait-AgentHealthy hard-fails with an
    # Event-Viewer pointer).
    Install-Service -PythonExe $python
    $health = Wait-AgentHealthy -Prefix $Script:Prefix -TimeoutSec 60
    Show-PairingQrFromService -Prefix $Script:Prefix -Version $version -Health $health

    Write-Completion -Version $version
}

Main
