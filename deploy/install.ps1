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
    $passthrough = @(
        'MOBAIDE_AGENT_PREFIX', 'MOBAIDE_AGENT_MANIFEST_URL',
        'MOBAIDE_AGENT_TRUST_KEY_B64', 'MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH',
        'MOBAIDE_AGENT_INSTALL_URL', 'MOBAIDE_AGENT_SKIP_INSTALL_QR',
        'MOBAIDE_AGENT_SKIP_PYTHON_INSTALL'
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
    & winget install --id Python.Python.3.12 --silent `
        --accept-package-agreements --accept-source-agreements `
        --scope machine --disable-interactivity
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
    $version = & $PythonExe -c "import json,sys; print(json.load(open(sys.argv[1]))['body']['version'])" $ManifestPath
    if (-not $version) { Write-Fail "Manifest is missing 'version'" }
    $version = $version.Trim()
    $releaseDir = Join-Path (Join-Path $Script:Prefix "releases") $version

    Write-Info "Staging release $version into $releaseDir"
    if (Test-Path $releaseDir) { Remove-Item -Recurse -Force $releaseDir }
    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null

    $tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("mobaide-agent-" + [guid]::NewGuid()))
    try {
        # The script prints exactly one line — the staged wheel's full path.
        # pip 25.2+ enforces PEP 427 wheel-filename validation, so the wheel
        # is staged under its canonical name-version-pyver-abi-platform
        # filename derived from the manifest's `wheel_url`.
        $stageScript = @'
import hashlib, json, posixpath, shutil, sys, tarfile, urllib.parse, urllib.request
from pathlib import Path

manifest_path, tmpdir, release_dir = sys.argv[1:4]
record = json.loads(Path(manifest_path).read_text())
body = record["body"]

def fetch(url, dest):
    if url.startswith("file://") or url.startswith("/"):
        shutil.copyfile(url.removeprefix("file://"), dest)
        return
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)

def verify_sha256(path, expected):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        sys.exit(f"sha256 mismatch for {path}")

def wheel_basename(url):
    if url.startswith("file://"):
        name = Path(url.removeprefix("file://")).name
    else:
        name = posixpath.basename(urllib.parse.urlparse(url).path)
    if any(ch in name for ch in ("/", "\\", "\x00")):
        sys.exit(
            f"manifest wheel_url basename contains path-separator chars: {name!r} (from {url!r})"
        )
    if not name.endswith(".whl") or name.count("-") < 4:
        sys.exit(
            f"manifest wheel_url has non-PEP427 filename: {name!r} (from {url!r})"
        )
    return name

tarball = Path(tmpdir) / "agent.tar.gz"
wheel = Path(tmpdir) / wheel_basename(body["wheel_url"])
fetch(body["tarball_url"], tarball)
verify_sha256(tarball, body["tarball_sha256"])
fetch(body["wheel_url"], wheel)
verify_sha256(wheel, body["wheel_sha256"])

with tarfile.open(tarball, mode="r:gz") as tar:
    tar.extractall(release_dir, filter="data")
print(wheel)
'@
        $wheel = Invoke-PythonScript -PythonExe $PythonExe -Script $stageScript -Arguments @($ManifestPath, "$tmp", $releaseDir)
        if ($LASTEXITCODE -ne 0) { Write-Fail "Staging failed" }
        $wheel = ($wheel | Out-String).Trim()

        $venv = Join-Path $releaseDir "venv"
        Write-Info "Creating venv in $venv"
        & $PythonExe -m venv $venv
        $venvPip = Join-Path $venv "Scripts\pip.exe"
        & $venvPip install --no-cache-dir --upgrade pip
        & $venvPip install --no-cache-dir $wheel
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
    Write-Info "Writing prepared agent .env via init-env"
    & $releasePython -m mobaide.agent.config init-env --prefix $Script:Prefix
    if ($LASTEXITCODE -ne 0) { Write-Fail "init-env failed (exit $LASTEXITCODE)" }
}

function Write-PairingQr {
    param($Version)
    # Best-effort: render a pairing QR for the operator to scan from the
    # Mobaide mobile app before the service starts. Non-fatal — the
    # boot-time autorun retries on its own.
    if ($env:MOBAIDE_AGENT_SKIP_INSTALL_QR) { return }
    $releaseDir = Join-Path (Join-Path $Script:Prefix "releases") $Version
    $releaseScripts = Join-Path $releaseDir "venv\Scripts"
    $mobaideAgent = Join-Path $releaseScripts "mobaide-agent.exe"
    if (-not (Test-Path $mobaideAgent)) {
        $mobaideAgent = Join-Path $releaseScripts "mobaide-agent"
    }
    Write-Info "Rendering pairing QR (scan from the Mobaide mobile app)"
    & $mobaideAgent pair-qr --print --prefix $Script:Prefix
    if ($LASTEXITCODE -ne 0) {
        Write-Info "QR render failed; the agent will re-render at boot."
    }
}

function Install-Service {
    param($Version)
    $launcherDir = Join-Path $Script:Prefix "bin"
    New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null
    $launcherCmd = Join-Path $launcherDir "mobaide-agent-launcher.cmd"
    $launcherBody = @"
@echo off
setlocal
set MOBAIDE_AGENT_PREFIX=$Script:Prefix
for /f %%v in (%MOBAIDE_AGENT_PREFIX%\current_version) do set CLAUDE_VERSION=%%v
"%MOBAIDE_AGENT_PREFIX%\releases\%CLAUDE_VERSION%\venv\Scripts\python.exe" -m mobaide.launcher
"@
    Set-Content -Path $launcherCmd -Value $launcherBody

    $existing = Get-Service -Name $Script:ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Info "Restarting existing $Script:ServiceName service"
        Restart-Service -Name $Script:ServiceName
        return
    }

    Write-Info "Registering $Script:ServiceName service"
    & sc.exe create $Script:ServiceName binPath= "cmd.exe /c `"$launcherCmd`"" start= auto DisplayName= "Mobaide Agent" | Out-Null
    # Three SCM-driven restarts at 5s spacing — the launcher rolls back
    # on its third boot attempt within the same window. reset=600 keeps
    # the failure counter alive long enough for the rolled-back release
    # to prove stable before SCM forgets the prior crash burst.
    & sc.exe failure $Script:ServiceName reset= 600 actions= restart/5000/restart/5000/restart/5000 | Out-Null
    Start-Service -Name $Script:ServiceName
}

function Write-Completion {
    param($Version)
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  Mobaide agent installed"
    Write-Host "  Prefix:  $Script:Prefix"
    Write-Host "  Version: $Version"
    Write-Host "  Service: $Script:ServiceName"
    Write-Host "============================================"
}

function Main {
    Assert-AdminOrElevate
    Write-Info "Installing mobaide_agent into $Script:Prefix"
    New-Item -ItemType Directory -Path $Script:Prefix -Force | Out-Null

    $python = Resolve-Python
    # Order: bootstrap key (env var or central_server fetch) -> pin to disk
    # so the agent picks it up at runtime -> fetch manifest -> verify it
    # against the same key -> stage. Initialize-TrustKey fails the install
    # with an actionable message if neither path yields a key.
    Initialize-TrustKey -PythonExe $python
    Write-TrustKey

    $manifest = New-TemporaryFile
    try {
        Get-Manifest -PythonExe $python -ManifestPath $manifest
        Test-ManifestSignature -PythonExe $python -ManifestPath $manifest
        $version = Install-Release -PythonExe $python -ManifestPath $manifest
    } finally {
        Remove-Item $manifest -ErrorAction SilentlyContinue
    }

    Write-AgentEnv -Version $version
    Write-PairingQr -Version $version

    Install-Service -Version $version
    Write-Completion -Version $version
}

Main
