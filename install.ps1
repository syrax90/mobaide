# Mobaide agent install script (Windows).
#
# Usage:
#     irm https://get.mobaide.com/install.ps1 | iex
#
# Self-elevates via UAC if run unelevated (Scheduled Task registration and
# %ProgramFiles%\MobaideAgent writes both require admin). Auto-installs
# Python 3.12 via winget if no qualifying interpreter is on PATH.
#
# Installs the agent as a per-user Scheduled Task ``MobaideAgent`` pinned
# to the installing user with ``-LogonType Interactive``, triggered
# ``-AtLogOn``. The task runs in the user's session so the Claude CLI in
# the agent's PTY sees the user's ``%USERPROFILE%`` (``C:\Users\<u>``) and
# the user's ``~/.claude`` credentials / skills. Requires an interactive
# logon: the task starts at logon and stops on logoff (lock-screen is
# fine). Headless / unattended hosts are out of scope for this installer.
#
# Idempotent: re-running upgrades the agent in place. Each release is staged
# into a sibling releases\<version>\ directory and the current_version
# pointer is rewritten atomically; the supervisor boots into whichever
# release is current.
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
# This file's path when run as `-File install.ps1` or `& .\install.ps1`;
# empty under `irm | iex`, even when that runs inside another script.
$Script:InstallerPath = $PSCommandPath
# Scheduled-Task supervisor URL. Co-versioned with install.ps1 itself: the
# publish-gh-pages workflow ships both files together on every push to
# main, so an installer change that depends on a new supervisor API can
# never desync from the module it ships against. Loaded into the Scheduled
# Task at install time and stored at <prefix>\svc\mobaide_launcher.py —
# outside any release dir, so the registered task ``Execute`` path stays
# stable across upgrades and rollbacks.
$Script:WindowsLauncherUrl = if ($env:MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL) {
    $env:MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL
} else {
    "https://get.mobaide.com/windows_launcher.py"
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

# The command the elevated PowerShell runs: the relevant MOBAIDE_* env vars
# reinstated, then the installer. Under `irm | iex` the script is not on disk,
# so it is fetched again from InstallUrl; run from a file, that same file runs,
# so a local or modified copy is not swapped for the published one.
function Get-ElevatedInstallCommand {
    $passthrough = @(
        'MOBAIDE_AGENT_PREFIX', 'MOBAIDE_AGENT_MANIFEST_URL',
        'MOBAIDE_AGENT_TRUST_KEY_B64', 'MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH',
        'MOBAIDE_AGENT_INSTALL_URL', 'MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL',
        'MOBAIDE_AGENT_SKIP_INSTALL_QR', 'MOBAIDE_AGENT_SKIP_PYTHON_INSTALL',
        'MOBAIDE_AGENT_CLAUDE_BIN_HINT', 'MOBAIDE_AGENT_CODEX_BIN_HINT',
        'MOBAIDE_AGENT_AGY_BIN_HINT', 'MOBAIDE_AGENT_COPILOT_BIN_HINT'
    )
    $envStmts = foreach ($name in $passthrough) {
        $val = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($val) {
            $escaped = $val -replace "'", "''"
            "`$env:$name = '$escaped'"
        }
    }
    $prefix = if ($envStmts) { ($envStmts -join '; ') + '; ' } else { '' }
    if ($Script:InstallerPath) {
        $pathEscaped = $Script:InstallerPath -replace "'", "''"
        return "$prefix" + "& '$pathEscaped'"
    }
    $urlEscaped = $Script:InstallUrl -replace "'", "''"
    return "$prefix" + "irm '$urlEscaped' | iex"
}

# `irm | iex` runs the script inline in the caller's PowerShell, so we cannot
# use `#Requires -RunAsAdministrator` (which errors confusingly before our own
# messages run). Instead: detect non-admin and re-launch via UAC with
# `Start-Process -Verb RunAs`, running Get-ElevatedInstallCommand.
function Assert-AdminOrElevate {
    if (Test-IsAdmin) { return }
    Write-Info "Not running elevated; relaunching under UAC to register the Scheduled Task."
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
    # Codex CLI counterpart: discover the optional codex binary under the
    # same originating-user env and carry it across the UAC boundary via
    # MOBAIDE_AGENT_CODEX_BIN_HINT, identically to the claude path.
    $codexBin = Find-CodexBinary
    if ($codexBin) {
        $env:MOBAIDE_AGENT_CODEX_BIN_HINT = $codexBin
    }
    # Antigravity CLI counterpart: discover the optional agy binary under the
    # same originating-user env and carry it across the UAC boundary via
    # MOBAIDE_AGENT_AGY_BIN_HINT, identically to the claude / codex paths.
    $agyBin = Find-AgyBinary
    if ($agyBin) {
        $env:MOBAIDE_AGENT_AGY_BIN_HINT = $agyBin
    }
    # GitHub Copilot CLI counterpart, carried across the UAC boundary via
    # MOBAIDE_AGENT_COPILOT_BIN_HINT in the same way.
    $copilotBin = Find-CopilotBinary
    if ($copilotBin) {
        $env:MOBAIDE_AGENT_COPILOT_BIN_HINT = $copilotBin
    }
    $cmd = Get-ElevatedInstallCommand
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

# Derive the path to ``pythonw.exe`` (GUI subsystem — no console allocated)
# from a resolved ``python.exe`` path. Required for the Scheduled Task
# action's ``-Execute`` so logon / install does NOT pop a transient
# console window into the user's session.
#
# Naming convention (CPython distribution): ``python.exe`` → ``pythonw.exe``;
# ``py.exe`` (Python Launcher) → ``pyw.exe``. The console / GUI variants
# always live side-by-side in the same directory. We probe the
# canonical name first; on miss, fall back to ``<dir>\pythonw.exe``
# (covers older distributions and venv-redirector layouts). Hard-fail
# loudly if neither exists — silently downgrading to ``python.exe``
# defeats the whole hidden-process design.
function Resolve-Pythonw {
    param([Parameter(Mandatory=$true)] [string] $PythonExe)
    $dir = [System.IO.Path]::GetDirectoryName($PythonExe)
    $name = [System.IO.Path]::GetFileNameWithoutExtension($PythonExe)
    $pythonwName = if ($name -eq 'py') { 'pyw.exe' } else { $name + 'w.exe' }
    $candidate = Join-Path $dir $pythonwName
    if (Test-Path -LiteralPath $candidate) {
        Write-Info "Using $candidate for the Scheduled Task (no console window)."
        return $candidate
    }
    $fallback = Join-Path $dir "pythonw.exe"
    if (Test-Path -LiteralPath $fallback) {
        Write-Info "Using $fallback for the Scheduled Task (no console window)."
        return $fallback
    }
    Write-Fail @"
Could not find a GUI-subsystem Python (pythonw.exe / pyw.exe) alongside
$PythonExe. The per-user Scheduled Task action must launch a no-console
Python so the agent does not pop a window into the user's session at
logon. Re-install Python from https://www.python.org/downloads/release/python-3120/
(the standard distribution ships pythonw.exe next to python.exe), or
set MOBAIDE_AGENT_SKIP_PYTHON_INSTALL=1 and point your interpreter at a
distribution that includes pythonw.exe.
"@
}

# Locate the user's claude.cmd at install time, BEFORE the UAC relaunch.
# The elevated session that Start-Process -Verb RunAs creates may not
# inherit the originating user's PATH (especially on "Run as different
# user" consent), and even when it does, the absolute path written into
# .env is more robust than a future PATH lookup: a regular user who
# later changes their PATH or removes the npm-global directory still
# has a working claude binary. shutil.which("claude.cmd") inside the
# agent would otherwise return None and session creation 503s with
# claude_cli_missing=true. We capture the path under the user's env
# and pin it into .env so the agent always resolves it.
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

# Optional Codex CLI counterpart of Find-ClaudeBinary. Codex ships as the
# npm-global package @openai/codex, so it resolves from the same PATH and
# %APPDATA%\npm locations; the same WindowsApps App Execution Alias guard
# applies. Returns the absolute codex path, or $null if not found. Unlike
# Claude, a miss is not warned — Codex is the optional secondary agent.
function Find-CodexBinary {
    foreach ($name in @('codex.cmd', 'codex.exe', 'codex')) {
        $hit = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $hit) { continue }
        if ($hit.Source -like "*\WindowsApps\*") { continue }
        return $hit.Source
    }
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:APPDATA) {
        $candidates.Add((Join-Path $env:APPDATA 'npm\codex.cmd'))
        $candidates.Add((Join-Path $env:APPDATA 'npm\codex.exe'))
    }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'npm\codex.cmd'))
    }
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE 'AppData\Roaming\npm\codex.cmd'))
    }
    $candidates.Add('C:\ProgramData\npm\codex.cmd')
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'nodejs\codex.cmd'))
    }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            return (Resolve-Path -LiteralPath $c).ProviderPath
        }
    }
    return $null
}

# Optional Antigravity CLI counterpart of Find-ClaudeBinary / Find-CodexBinary.
# Unlike Claude/Codex, agy is a standalone binary (not npm): its installer
# lands in %USERPROFILE%\.local\bin, so the PATH-miss fallbacks probe that and
# the conventional Programs locations instead of the npm dirs; the same
# WindowsApps App Execution Alias guard applies. Returns the absolute agy
# path, or $null if not found. Like Codex, a miss is not warned — optional.
function Find-AgyBinary {
    foreach ($name in @('agy.exe', 'agy.cmd', 'agy')) {
        $hit = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $hit) { continue }
        if ($hit.Source -like "*\WindowsApps\*") { continue }
        return $hit.Source
    }
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE '.local\bin\agy.exe'))
        $candidates.Add((Join-Path $env:USERPROFILE '.local\bin\agy.cmd'))
    }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Antigravity\agy.exe'))
    }
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'Antigravity\agy.exe'))
    }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            return (Resolve-Path -LiteralPath $c).ProviderPath
        }
    }
    return $null
}

# Optional GitHub Copilot CLI counterpart of Find-CodexBinary. The check is
# "is the copilot command available": PATH first, then the two places its
# installers use, the npm-global folder (npm install -g @github/copilot) and
# the WinGet link folder (winget install GitHub.Copilot). Only a real
# executable counts: a PowerShell alias or function named copilot has no path
# the agent could start. Returns the absolute copilot path, or $null if not
# found. A miss is not warned: Copilot is optional.
function Find-CopilotBinary {
    foreach ($name in @('copilot.exe', 'copilot.cmd', 'copilot')) {
        $hit = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $hit) { continue }
        if ($hit.CommandType -ne 'Application') { continue }
        if ($hit.Source -like "*\WindowsApps\*") { continue }
        return $hit.Source
    }
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:APPDATA) {
        $candidates.Add((Join-Path $env:APPDATA 'npm\copilot.cmd'))
    }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'npm\copilot.cmd'))
    }
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE 'AppData\Roaming\npm\copilot.cmd'))
    }
    $candidates.Add('C:\ProgramData\npm\copilot.cmd')
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'nodejs\copilot.cmd'))
    }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\copilot.exe'))
    }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            return (Resolve-Path -LiteralPath $c).ProviderPath
        }
    }
    return $null
}

# Write MOBAIDE_COPILOT_BIN into an existing agent .env unless the operator
# already set it. It uses set-env, not an init-env argument: set-env takes any
# key, so this installer also works with an agent release that is older than
# Copilot support (an unknown init-env argument would fail the install).
function Set-CopilotBinIfMissing {
    param(
        [Parameter(Mandatory=$true)] [string] $EnvFile,
        [Parameter(Mandatory=$true)] [string] $ReleasePython,
        [Parameter(Mandatory=$true)] [string] $CopilotBin
    )
    $existing = Read-EnvValue -Path $EnvFile -Key 'MOBAIDE_COPILOT_BIN'
    if ($existing) {
        Write-Info "MOBAIDE_COPILOT_BIN already set in $EnvFile; leaving as-is."
        return
    }
    Write-Info "Writing MOBAIDE_COPILOT_BIN=$CopilotBin into $EnvFile"
    & $ReleasePython -m mobaide.agent.config set-env --prefix $Script:Prefix `
        --key MOBAIDE_COPILOT_BIN --value $CopilotBin
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "set-env failed (exit $LASTEXITCODE); edit $EnvFile manually and restart $Script:ServiceName."
    }
}

# Both auto-install paths (winget and python.org) edit the persistent PATH
# but not the in-flight process environment. Recompose the in-session PATH
# from the Machine + User registry scopes so Find-Python can resolve the
# freshly-installed interpreter without a shell restart.
function Update-CurrentSessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Fast path: winget install Python.Python.3.12 at machine scope so the
# Scheduled Task's `Execute` path (pinned to this Python at install
# time) stays valid across user-account changes and is discoverable
# from any logged-in session. Returns $true on success, $false on any
# failure (caller falls back to the python.org installer).
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
        # --scope machine choice. The Scheduled Task action's Execute
        # path is recorded at install time; using a machine-scope
        # install keeps that path valid across user-account changes
        # and discoverable from any logged-in session. SimpleInstall=1
        # suppresses the optional-features page; Include_test=0 trims
        # the install footprint.
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

# Stops the Scheduled Task and every process still running from $Prefix.
# Stop-ScheduledTask alone is not enough: a launcher or agent left behind by an
# earlier install outlives its task, keeps serving, and keeps the release dir
# locked (a running .exe or loaded .dll cannot be moved or deleted).
# Returns $true when the task was running or any process was stopped.
function Stop-AgentProcesses {
    param([Parameter(Mandatory=$true)] [string] $Prefix)
    $stopped = $false
    $task = Get-ScheduledTask -TaskName $Script:ServiceName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $Script:ServiceName -ErrorAction SilentlyContinue
        $stopped = $true
    }
    $root = $Prefix.TrimEnd('\') + '\'
    $procs = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
    })
    foreach ($proc in $procs) {
        Write-Info "Stopping $($proc.Name) (PID $($proc.ProcessId)) running from $Prefix"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped = $true
    }
    if ($procs) {
        Wait-Process -Id $procs.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
    }
    return $stopped
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
    # Reinstall safely over an existing same-version release: move the live dir
    # ASIDE rather than deleting it up front. The finally below restores it if any
    # staging step (move / fresh-dir / download / checksum / extraction / venv /
    # pip) fails, so a single-release install is never left pointing at a
    # deleted/half-built dir with no complete sibling for supervisor recovery.
    # Building into a FRESH empty dir also guarantees no stale completeness signal
    # survives into the rebuild window (the prior '.installed' AND the prior
    # console script that `python -m venv` leaves behind are gone). The backup uses
    # the updater's '.old-<v>-<pid>' scratch convention, so it is skipped by every
    # release selector and reclaimed by the agent's _sweep_scratch_dirs if leaked.
    # Mirrors deploy/install.sh's stage_release and the updater's _swap_into_place.
    $releaseBackup = $null
    $staged = $false
    $agentStopped = $false
    # $tmp is created BEFORE the try so the finally always has it to remove; the
    # move-aside runs INSIDE the try so a failing Move-Item / New-Item is covered
    # by the same restore path as a later download / pip failure.
    $tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("mobaide-agent-" + [guid]::NewGuid()))
    try {
        if (Test-Path $releaseDir) {
            # Only a same-version reinstall moves a release the agent may be
            # running from; an upgrade stages beside it and Install-Service
            # stops the agent later, keeping the download window online.
            $agentStopped = Stop-AgentProcesses -Prefix $Script:Prefix
            $releaseBackup = Join-Path (Join-Path $Script:Prefix "releases") ".old-$version-$PID"
            if (Test-Path $releaseBackup) { Remove-Item -Recurse -Force $releaseBackup }
            Move-Item -Force $releaseDir $releaseBackup
        }
        New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null

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
        # Base-interpreter hardening (mirrors install.sh): when $PythonExe is
        # itself a venv, sys._base_executable is the real interpreter it was
        # created from. Basing the release venv off that stable interpreter and
        # clearing any active VIRTUAL_ENV/PYTHONHOME keeps the venv's
        # interpreter from symlinking into a transient venv whose later removal
        # would brick the release with a 'bad interpreter' shim.
        $basePython = (& $PythonExe -c 'import sys; print(sys._base_executable or sys.executable)' 2>$null | Out-String).Trim()
        if (-not $basePython) { $basePython = $PythonExe }
        Remove-Item Env:\VIRTUAL_ENV -ErrorAction SilentlyContinue
        Remove-Item Env:\PYTHONHOME -ErrorAction SilentlyContinue
        # Every `&`-invoked native command emits its stdout onto the
        # surrounding function's success pipeline. Without `| Out-Host`,
        # pip's `[notice]` lines and `python -m venv`'s chatter get glued
        # onto Install-Release's final `return $version`, and `Main`
        # receives an `object[]` instead of a string. Downstream
        # `Join-Path -ChildPath $Version` then crashes with
        # `Cannot convert 'System.Object[]' to ... 'ChildPath'`.
        & $basePython -m venv $venv | Out-Host
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
        # Mark the release complete only after pip + the wheel are installed, so
        # the supervisor recovery never selects a half-built release. Keep
        # '.installed' in sync with install_paths.is_release_complete.
        Set-Content -Path (Join-Path $releaseDir ".installed") -Value $version -NoNewline
        $staged = $true
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        # Settle the moved-aside previous release. Write-Fail (Write-Error; exit 1)
        # unwinds through this finally just like the $tmp cleanup above relies on,
        # so on any pre-sentinel failure ($staged still $false) restore the live
        # release; on success discard the backup. Guarded on Test-Path so a
        # Move-Item that threw without moving (atomic-rename failure — the backup
        # was never created) does not trigger a bogus restore. No-op when there was
        # no existing release to move aside.
        if ($releaseBackup -and (Test-Path $releaseBackup)) {
            if ($staged) {
                Remove-Item -Recurse -Force $releaseBackup -ErrorAction SilentlyContinue
            } else {
                Remove-Item -Recurse -Force $releaseDir -ErrorAction SilentlyContinue
                Move-Item -Force $releaseBackup $releaseDir
            }
        }
        # A failed reinstall leaves the previous release in place; bring the
        # agent this function stopped back up on it.
        if ($agentStopped -and -not $staged) {
            Start-ScheduledTask -TaskName $Script:ServiceName -ErrorAction SilentlyContinue
        }
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
    Stop-ScheduledTask  -TaskName $Script:ServiceName
    Start-ScheduledTask -TaskName $Script:ServiceName
"@
    }

    # Codex CLI counterpart (optional secondary agent): prefer the pre-UAC
    # hint, fall back to a fresh probe. A miss is logged at Info, not Warn —
    # most operators run Claude only, and the agent simply leaves
    # MOBAIDE_CODEX_BIN unset until a Codex session is requested.
    $codexBin = $env:MOBAIDE_AGENT_CODEX_BIN_HINT
    if (-not $codexBin) { $codexBin = Find-CodexBinary }
    if ($codexBin) {
        Write-Info "Detected Codex CLI at $codexBin"
    } else {
        Write-Info "Codex CLI not detected; leaving MOBAIDE_CODEX_BIN unset (set it in $Script:Prefix\mobaide_agent\.env if you use Codex)."
    }

    # Antigravity CLI counterpart (optional managed agent): same hint-then-probe
    # discovery as Codex, and the same soft Info-on-miss — Antigravity is
    # optional, so a miss leaves MOBAIDE_AGY_BIN unset until an Antigravity
    # session is run. Note agy has no --version flag; presence is the only check.
    $agyBin = $env:MOBAIDE_AGENT_AGY_BIN_HINT
    if (-not $agyBin) { $agyBin = Find-AgyBinary }
    if ($agyBin) {
        Write-Info "Detected Antigravity CLI at $agyBin"
    } else {
        Write-Info "Antigravity CLI (agy) not detected; leaving MOBAIDE_AGY_BIN unset (set it in $Script:Prefix\mobaide_agent\.env if you use Antigravity)."
    }

    # GitHub Copilot CLI counterpart (optional managed agent): the same
    # hint-then-probe discovery and the same soft Info-on-miss.
    $copilotBin = $env:MOBAIDE_AGENT_COPILOT_BIN_HINT
    if (-not $copilotBin) { $copilotBin = Find-CopilotBinary }
    if ($copilotBin) {
        Write-Info "Detected GitHub Copilot CLI at $copilotBin"
    } else {
        Write-Info "GitHub Copilot CLI not detected; leaving MOBAIDE_COPILOT_BIN unset (set it in $Script:Prefix\mobaide_agent\.env if you use GitHub Copilot)."
    }

    $envFile = Join-Path (Join-Path $Script:Prefix "mobaide_agent") ".env"
    $envExists = Test-Path -LiteralPath $envFile

    if (-not $envExists) {
        Write-Info "Writing prepared agent .env via init-env"
        $extraArgs = @()
        if ($claudeBin) { $extraArgs += @('--claude-bin', $claudeBin) }
        if ($codexBin) { $extraArgs += @('--codex-bin', $codexBin) }
        if ($agyBin) { $extraArgs += @('--agy-bin', $agyBin) }
        & $releasePython -m mobaide.agent.config init-env --prefix $Script:Prefix @extraArgs
        if ($LASTEXITCODE -ne 0) { Write-Fail "init-env failed (exit $LASTEXITCODE)" }
        if ($copilotBin) {
            Set-CopilotBinIfMissing -EnvFile $envFile -ReleasePython $releasePython -CopilotBin $copilotBin
        }
        return
    }

    # Existing .env (re-install over a paired agent): leave credentials
    # alone. Surgically backfill MOBAIDE_CLAUDE_BIN / MOBAIDE_CODEX_BIN /
    # MOBAIDE_AGY_BIN only if not already set — an operator's manual edit
    # always wins. This implements the "Surgical set-env only" policy. Each
    # CLI is backfilled independently: a missing Claude path must not skip
    # the Codex or Antigravity backfill (and vice versa). MOBAIDE_COPILOT_BIN
    # follows the same rule through Set-CopilotBinIfMissing.
    Write-Info "Existing $envFile preserved (re-install)."
    if ($claudeBin) {
        $existingClaudeBin = Read-EnvValue -Path $envFile -Key 'MOBAIDE_CLAUDE_BIN'
        if ($existingClaudeBin) {
            Write-Info "MOBAIDE_CLAUDE_BIN already set in $envFile; leaving as-is."
        } else {
            Write-Info "Backfilling MOBAIDE_CLAUDE_BIN=$claudeBin into $envFile"
            & $releasePython -m mobaide.agent.config set-env --prefix $Script:Prefix `
                --key MOBAIDE_CLAUDE_BIN --value $claudeBin
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "set-env failed (exit $LASTEXITCODE); edit $envFile manually and restart $Script:ServiceName."
            }
        }
    }
    if ($codexBin) {
        $existingCodexBin = Read-EnvValue -Path $envFile -Key 'MOBAIDE_CODEX_BIN'
        if ($existingCodexBin) {
            Write-Info "MOBAIDE_CODEX_BIN already set in $envFile; leaving as-is."
        } else {
            Write-Info "Backfilling MOBAIDE_CODEX_BIN=$codexBin into $envFile"
            & $releasePython -m mobaide.agent.config set-env --prefix $Script:Prefix `
                --key MOBAIDE_CODEX_BIN --value $codexBin
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "set-env failed (exit $LASTEXITCODE); edit $envFile manually and restart $Script:ServiceName."
            }
        }
    }
    if ($agyBin) {
        $existingAgyBin = Read-EnvValue -Path $envFile -Key 'MOBAIDE_AGY_BIN'
        if ($existingAgyBin) {
            Write-Info "MOBAIDE_AGY_BIN already set in $envFile; leaving as-is."
        } else {
            Write-Info "Backfilling MOBAIDE_AGY_BIN=$agyBin into $envFile"
            & $releasePython -m mobaide.agent.config set-env --prefix $Script:Prefix `
                --key MOBAIDE_AGY_BIN --value $agyBin
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "set-env failed (exit $LASTEXITCODE); edit $envFile manually and restart $Script:ServiceName."
            }
        }
    }
    if ($copilotBin) {
        Set-CopilotBinIfMissing -EnvFile $envFile -ReleasePython $releasePython -CopilotBin $copilotBin
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
# or hard-fail with a diagnostic pointer. The bug this guards against:
# `Start-ScheduledTask` returns immediately once Task Scheduler
# accepts the start request, but the supervisor / launcher / agent
# chain can still crash before uvicorn opens the HTTP socket — and
# the operator only discovers the failure when the QR they scan
# never pairs. The 60s budget covers a cold-start that includes
# Python import + uvicorn bind on slow hosts; failing fast with a
# Task-Scheduler / log-file pointer is the difference between
# "obvious install bug" and "QR silently doesn't work".
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
Scheduled Task registered but /api/health did not respond within ${TimeoutSec}s.
The agent may have crashed at startup. Inspect:
    eventvwr.msc -> Applications and Services Logs -> Microsoft
        -> Windows -> TaskScheduler -> Operational
Or from PowerShell:
    Get-ScheduledTask     -TaskName MobaideAgent
    Get-ScheduledTaskInfo -TaskName MobaideAgent
    Get-Content "$Prefix\logs\agent.err.log" -Tail 50
    Get-Content "$Prefix\logs\agent.out.log" -Tail 100
"@
}

function Show-PairingQrFromService {
    param(
        [Parameter(Mandatory=$true)] [string] $Prefix,
        # Install-CliShim's result: the shim to run and the command to print.
        [Parameter(Mandatory=$true)] [hashtable] $Cli,
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
Agent is up but did not initiate pairing within ${PendingTimeoutSec}s
(no $pendingFile). The agent likely cannot reach central_server. Check:
    Test-NetConnection -ComputerName api.mobaide.com -Port 443
    Get-Content "$Prefix\logs\agent.err.log" -Tail 50
The agent is still running; once connectivity is restored it will
initiate a nonce on its own. Run: $($Cli.Command) show-qr
"@
    }
    Write-Info "Rendering pairing QR (scan from the Mobaide mobile app)"
    # ``--require-pending`` keeps this call from initiating a second
    # nonce (the running service has already initiated): two concurrent
    # /ws/pairing/{nonce} dials for the same nonce would race, and the
    # central server rejects the loser with WS close 4001. The flag
    # makes the CLI render-from-file only; on exit 7 (file vanished
    # between our Test-Path and the CLI's load) we hard-fail rather
    # than fall through to a fresh initiate.
    #
    # Rendered through the installed shim, so a broken shim fails the
    # install instead of the operator's later recovery attempt. No
    # --prefix: the shim exports the prefix it lives in.
    $shim = $Cli.Path
    & $shim show-qr --require-pending
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "mobaide-agent show-qr --require-pending failed (exit $LASTEXITCODE). Run: $($Cli.Command) show-qr"
    }
}

function Install-Service {
    param($PythonExe, $PythonwExe)
    # Registers the agent as a per-user Scheduled Task pinned to the
    # installing user. Replaces the prior SCM-service-as-LocalSystem
    # design: that model gave the agent process tree the system profile
    # (%USERPROFILE% = C:\Windows\system32\config\systemprofile), which
    # hid the user's ~/.claude credentials and skills from the Claude
    # CLI in the agent's PTY. Scheduled Task with -LogonType Interactive
    # runs the agent in the user's session — no password capture, real
    # %USERPROFILE%, mirrors Linux's $SUDO_USER semantics.
    #
    # ``$PythonwExe`` (GUI subsystem) is the action target so the task
    # never allocates a console window. Console / install-time work
    # (e.g., wheel install) still uses ``$PythonExe`` in PowerShell's
    # own console.
    #
    # Function name preserved (`Install-Service`) so Main's call site
    # and the ordering pin in tests/test_install_ps1.py stay stable.
    $svcDir = Join-Path $Script:Prefix "svc"
    $svcEntry = Join-Path $svcDir "mobaide_launcher.py"

    New-Item -ItemType Directory -Path $svcDir -Force | Out-Null

    # Migration: a previous install may have left a LocalSystem SCM
    # service named MobaideAgent on the host. Detect and remove it
    # before registering the new Scheduled Task — same name, two
    # different OS-level objects. sc.exe delete is the idempotent
    # primitive (Get-Service returns nothing if SCM has already
    # cleared the entry, so the Get-Service guard makes the cleanup
    # a no-op on a clean machine).
    $legacyService = Get-Service -Name $Script:ServiceName -ErrorAction SilentlyContinue
    if ($legacyService) {
        Write-Info "Removing legacy LocalSystem SCM service $Script:ServiceName from prior install"
        try {
            Stop-Service -Name $Script:ServiceName -Force -ErrorAction Stop
        } catch {
            Write-Warn "Could not stop legacy service (continuing): $($_.Exception.Message)"
        }
        & sc.exe delete $Script:ServiceName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "sc.exe delete $Script:ServiceName returned exit $LASTEXITCODE; continuing."
        }
    }

    # Clean up the legacy svc venv (pywin32 binaries from the prior
    # design). Best-effort — files held open by a still-running
    # legacy service would already have been freed by Stop-Service
    # above. Failure is logged and ignored.
    $legacySvcVenv = Join-Path $svcDir "venv"
    if (Test-Path $legacySvcVenv) {
        Write-Info "Removing legacy service venv at $legacySvcVenv"
        Remove-Item -Recurse -Force $legacySvcVenv -ErrorAction SilentlyContinue
    }
    $legacyServiceModule = Join-Path $svcDir "mobaide_service.py"
    if (Test-Path $legacyServiceModule) {
        Remove-Item -Force $legacyServiceModule -ErrorAction SilentlyContinue
    }

    # Idempotency: if a previous run of THIS installer already
    # registered the Scheduled Task, unregister it before re-creating
    # so Register-ScheduledTask -Force has a clean slate (and so a
    # changed Execute / Argument path doesn't leave a stale entry).
    # Stopping by prefix also catches processes an earlier install left behind,
    # which would otherwise keep running beside the agent started below.
    Stop-AgentProcesses -Prefix $Script:Prefix | Out-Null
    $existingTask = Get-ScheduledTask -TaskName $Script:ServiceName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Write-Info "Unregistering existing Scheduled Task $Script:ServiceName before re-registering"
        Unregister-ScheduledTask -TaskName $Script:ServiceName -Confirm:$false -ErrorAction Stop
    }

    # Fetch the supervisor module from the same distribution channel as
    # install.ps1 itself (gh-pages → get.mobaide.com). publish-gh-pages
    # ships deploy/install.ps1 and deploy/windows_launcher.py in the
    # same push, so the two cannot desync. The supervisor is stdlib-only
    # (no pywin32 needed), so the previously-required svc venv is gone.
    Write-Info "Downloading supervisor module from $Script:WindowsLauncherUrl"
    Invoke-AgentDownload -Url $Script:WindowsLauncherUrl -OutFile $svcEntry -UserAgent "mobaide-agent-installer"
    if (-not (Test-Path $svcEntry)) {
        Write-Fail "Supervisor module download failed: $svcEntry does not exist after fetch from $Script:WindowsLauncherUrl"
    }
    Write-Info "Wrote supervisor module to $svcEntry"

    Write-Info "Registering Scheduled Task $Script:ServiceName for user $env:USERNAME"
    # -LogonType Interactive runs in the installing user's session at
    # logon — gives the agent a real %USERPROFILE% so Claude finds the
    # user's ~/.claude. -RunLevel Highest lets the agent write under
    # %ProgramFiles%\MobaideAgent (logs, current_version) without a
    # per-call UAC prompt. -AtLogOn -User $env:USERNAME triggers the
    # task automatically every time the user logs in.
    #
    # Settings:
    #   -RestartCount/-RestartInterval: outer-layer restart on
    #     task-level failure. The supervisor's own exit-75 in-process
    #     loop handles the immediate post-pair / post-update respawn;
    #     Task Scheduler's 1-minute floor catches the cases where the
    #     supervisor itself crashes.
    #   -ExecutionTimeLimit (New-TimeSpan -Seconds 0): documented
    #     "no time limit" — without this, the task is killed at 72 h.
    #   -AllowStartIfOnBatteries / -DontStopIfGoingOnBatteries: the
    #     agent must keep running on laptops regardless of power state.
    $taskArg = "-u `"$svcEntry`" --prefix `"$Script:Prefix`""
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    # ``-Execute $PythonwExe`` (NOT ``$PythonExe``): the GUI-subsystem
    # variant of CPython does not request a console at process start,
    # so Task Scheduler launches the supervisor with no visible window
    # at logon or during install. Switching this to ``$PythonExe``
    # reintroduces the original "py.exe window pops up on boot" bug.
    $action    = New-ScheduledTaskAction -Execute $PythonwExe -Argument $taskArg
    $settings  = New-ScheduledTaskSettingsSet `
        -RestartCount 5 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
    Register-ScheduledTask -TaskName $Script:ServiceName `
        -Principal $principal -Trigger $trigger -Action $action -Settings $settings -Force | Out-Null

    # Defence-in-depth on silent-failure: re-check that the task is
    # registered before we hand off to Wait-AgentHealthy. Surfaces
    # the real problem at install time rather than as an opaque
    # 60-second polling timeout downstream.
    if (-not (Get-ScheduledTask -TaskName $Script:ServiceName -ErrorAction SilentlyContinue)) {
        Write-Fail "Scheduled Task $Script:ServiceName did not register. Re-run as Administrator and check Task Scheduler / Event Viewer."
    }

    # Start immediately instead of waiting for the next user logon —
    # the installer is interactive and the user expects the agent up
    # within seconds of install completion.
    Start-ScheduledTask -TaskName $Script:ServiceName
}

# Tells running programs (Explorer, and so every terminal opened from it)
# that the persistent environment changed. Best-effort: without it the new
# PATH entry still applies from the next sign-in.
function Send-EnvironmentChange {
    try {
        if (-not ('MobaideInstaller.NativeMethods' -as [type])) {
            Add-Type -Namespace MobaideInstaller -Name NativeMethods -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
        }
        $result = [UIntPtr]::Zero
        # HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG, 5 s timeout.
        [MobaideInstaller.NativeMethods]::SendMessageTimeout([IntPtr]0xffff, 0x1a, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result) | Out-Null
    } catch {
        Write-Warn "Could not announce the PATH change: $($_.Exception.Message). If a new terminal does not find mobaide-agent, sign out and back in."
    }
}

# Appends $Directory to the current user's persistent PATH unless an
# equivalent entry is already there, then refreshes this session's PATH.
# The raw value is read unexpanded and written back verbatim with the same
# value kind, so %VAR% entries survive: an [Environment]::SetEnvironmentVariable
# round-trip would expand them and store REG_SZ, and setx truncates PATH at
# 1024 characters. -SubKey exists for tests.
function Add-UserPathEntry {
    param(
        [Parameter(Mandatory=$true)] [string] $Directory,
        [string] $SubKey = 'Environment'
    )
    $wanted = $Directory.Trim('"').TrimEnd('\')
    $added = $false
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($SubKey)
    try {
        $raw = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $present = @($raw -split ';' | Where-Object {
            $_ -and ([Environment]::ExpandEnvironmentVariables($_).Trim('"').TrimEnd('\') -ieq $wanted)
        })
        if ($present.Count -eq 0) {
            if ($raw) {
                $kind = $key.GetValueKind('Path')
                $value = $raw.TrimEnd(';') + ';' + $Directory
            } else {
                $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
                $value = $Directory
            }
            $key.SetValue('Path', $value, $kind)
            $added = $true
        }
    } finally {
        $key.Close()
    }
    if ($added) {
        Send-EnvironmentChange
    }
    Update-CurrentSessionPath
}

# Puts `mobaide-agent` on PATH. The release venv's console script moves with
# every update and rollback, so the shim reads <prefix>\current_version on
# each call instead of pinning a release, and derives the prefix from its own
# location. It lives in the admin-only <prefix>\bin; the PATH entry goes to
# the installing user, the same account the Scheduled Task and the .env ACL
# belong to. Returns @{ Path = <shim>; Command = <what the banners print> }.
function Install-CliShim {
    param([Parameter(Mandatory=$true)] [string] $Prefix)
    $binDir = Join-Path $Prefix "bin"
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $shim = Join-Path $binDir "mobaide-agent.cmd"
    # Escape cmd.exe metacharacters for the echo line (a custom install URL
    # may carry & or %).
    $installUrl = ($Script:InstallUrl -replace '([\^&|<>])', '^$1') -replace '%', '%%'
    $lines = @(
        '@echo off'
        'rem mobaide-agent CLI shim, written by the Mobaide agent installer.'
        'setlocal EnableExtensions DisableDelayedExpansion'
        'for %%I in ("%~dp0..") do set "MOBAIDE_AGENT_PREFIX=%%~fI"'
        'set "version="'
        'if exist "%MOBAIDE_AGENT_PREFIX%\current_version" for /f "usebackq delims=" %%V in ("%MOBAIDE_AGENT_PREFIX%\current_version") do if not defined version set "version=%%V"'
        'if not defined version goto missing'
        'set "cli=%MOBAIDE_AGENT_PREFIX%\releases\%version%\venv\Scripts\mobaide-agent.exe"'
        'if not exist "%cli%" goto missing'
        '"%cli%" %*'
        'exit /b %ERRORLEVEL%'
        ':missing'
        '>&2 echo mobaide-agent: no Mobaide agent is installed at %MOBAIDE_AGENT_PREFIX%.'
        ">&2 echo Re-run the installer from PowerShell: irm $installUrl ^| iex"
        'exit /b 9009'
    )
    # ASCII with CRLF: cmd.exe misreads a UTF-8 BOM and can lose `goto`
    # labels in LF-only batch files.
    [System.IO.File]::WriteAllText($shim, (($lines -join "`r`n") + "`r`n"), [System.Text.Encoding]::ASCII)
    Write-Info "Installed the mobaide-agent command at $shim"

    $command = 'mobaide-agent'
    try {
        Add-UserPathEntry -Directory $binDir
    } catch {
        Write-Warn "Could not add $binDir to your PATH: $($_.Exception.Message)"
        $command = "& '$shim'"
    }
    return @{ Path = $shim; Command = $command }
}

function Write-Completion {
    param($Version, $Command)
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  Mobaide agent installed"
    Write-Host "  Prefix:  $Script:Prefix"
    Write-Host "  Version: $Version"
    Write-Host "  Task:    $Script:ServiceName (Running as $env:USERNAME)"
    Write-Host ""
    Write-Host "  Re-render QR:  $Command show-qr"
    Write-Host "  Re-pair:       $Command re-pair"
    Write-Host "  Restart:       Stop-ScheduledTask $Script:ServiceName; Start-ScheduledTask $Script:ServiceName"
    if ($Command -eq 'mobaide-agent') {
        Write-Host ""
        Write-Host "  Open a new terminal window to use mobaide-agent (PATH was updated for $env:USERNAME)."
    }
    Write-Host "============================================"
}

function Main {
    Assert-AdminOrElevate
    Write-Info "Installing mobaide_agent into $Script:Prefix"
    New-Item -ItemType Directory -Path $Script:Prefix -Force | Out-Null

    $python = Resolve-Python
    # Resolve the GUI-subsystem counterpart now (alongside python.exe) so a
    # missing pythonw.exe surfaces BEFORE we stage releases / register the
    # task. Failing here is far more debuggable than failing at logon.
    $pythonwExe = Resolve-Pythonw -PythonExe $python
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
    Install-Service -PythonExe $python -PythonwExe $pythonwExe
    $cli = Install-CliShim -Prefix $Script:Prefix
    $health = Wait-AgentHealthy -Prefix $Script:Prefix -TimeoutSec 60
    Show-PairingQrFromService -Prefix $Script:Prefix -Cli $cli -Health $health

    Write-Completion -Version $version -Command $cli.Command
}

Main
