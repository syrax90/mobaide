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

function Test-ManifestSignature {
    param($PythonExe, $ManifestPath)
    if (-not $Script:TrustKeyB64) {
        Write-Warn "MOBAIDE_AGENT_TRUST_KEY_B64 not set — skipping signature check (first-install bootstrap)."
        return
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
    if (-not $Script:TrustKeyB64) { return }
    $trustDir = Join-Path $Script:Prefix "trust"
    New-Item -ItemType Directory -Path $trustDir -Force | Out-Null
    Set-Content -Path (Join-Path $trustDir "central_server.pub") -Value $Script:TrustKeyB64 -NoNewline
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
    Write-TrustKey

    $python = Test-Python
    $manifest = New-TemporaryFile
    try {
        Get-Manifest -PythonExe $python -ManifestPath $manifest
        Test-ManifestSignature -PythonExe $python -ManifestPath $manifest
        $version = Install-Release -PythonExe $python -ManifestPath $manifest
    } finally {
        Remove-Item $manifest -ErrorAction SilentlyContinue
    }

    Install-Service -Version $version
    Write-Completion -Version $version
}

Main
