# Mobaide agent install script (Windows).
#
# Usage (PowerShell as Administrator):
#     irm https://get.mobaide.com/install.ps1 | iex
#
# Idempotent: re-running upgrades the agent in place. Each release is staged
# into a sibling releases\<version>\ directory and the current_version
# pointer is rewritten atomically; the launcher boots into whichever release
# is current.
#
# Wrapped in a single Main function called once at the bottom so a partial
# download (irm | iex) cannot execute half a script.

#Requires -RunAsAdministrator
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

function Write-Info  { param($Msg) Write-Host "[INFO]  $Msg" }
function Write-Warn  { param($Msg) Write-Warning $Msg }
function Write-Fail  { param($Msg) Write-Error $Msg; exit 1 }

function Test-Python {
    $candidates = @("py", "python", "python3")
    foreach ($cmd in $candidates) {
        $exe = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
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
    Write-Fail "Python 3.12+ is required."
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
    & $PythonExe -c $validateScript $Script:TrustKeyB64
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
    & $PythonExe -c $verifyScript $ManifestPath $Script:TrustKeyB64
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
        $wheel = & $PythonExe -c $stageScript $ManifestPath $tmp $releaseDir
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
    Write-Info "Installing mobaide_agent into $Script:Prefix"
    New-Item -ItemType Directory -Path $Script:Prefix -Force | Out-Null

    $python = Test-Python
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

    Install-Service -Version $version
    Write-Completion -Version $version
}

Main
