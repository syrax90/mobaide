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

# Python must not import modules from the current directory or from the
# folder of a helper script: a file named like a standard module there would
# run inside the checks below.
$env:PYTHONSAFEPATH = "1"

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
# Public half of the key that signs every release manifest (base64 Ed25519).
# The script carries it, so no server is asked which key to trust. Set
# MOBAIDE_AGENT_TRUST_KEY_B64 to install from your own release pipeline.
$Script:ReleasePublicKeyB64 = "f+cIAgdrhuXQtkHjRI5NSYjKAAc0aUWWZP7s7eIjunw="
# The oldest agent release this script installs: the first one whose signed
# data pins every dependency by hash. A server that offers an older signed
# release is refused. release_agent.py refuses a release below this number.
$Script:MinimumAgentVersion = "0.3.38"
$Script:TrustKeyB64 = $env:MOBAIDE_AGENT_TRUST_KEY_B64
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
        'MOBAIDE_AGENT_AGY_BIN_HINT', 'MOBAIDE_AGENT_COPILOT_BIN_HINT',
        'MOBAIDE_AGENT_CURSOR_BIN_HINT', 'MOBAIDE_AGENT_JUNIE_BIN_HINT',
        'MOBAIDE_AGENT_PI_BIN_HINT'
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
    # Cursor CLI counterpart, carried across the UAC boundary via
    # MOBAIDE_AGENT_CURSOR_BIN_HINT in the same way.
    $cursorBin = Find-CursorBinary
    if ($cursorBin) {
        $env:MOBAIDE_AGENT_CURSOR_BIN_HINT = $cursorBin
    }
    # Junie CLI counterpart, carried across the UAC boundary via
    # MOBAIDE_AGENT_JUNIE_BIN_HINT in the same way.
    $junieBin = Find-JunieBinary
    if ($junieBin) {
        $env:MOBAIDE_AGENT_JUNIE_BIN_HINT = $junieBin
    }
    # Pi counterpart, carried across the UAC boundary via
    # MOBAIDE_AGENT_PI_BIN_HINT in the same way.
    $piBin = Find-PiBinary
    if ($piBin) {
        $env:MOBAIDE_AGENT_PI_BIN_HINT = $piBin
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
# lands in %LOCALAPPDATA%\agy\bin, so the PATH-miss fallbacks probe that,
# %USERPROFILE%\.local\bin and the conventional Programs locations instead of the npm dirs; the same
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
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe'))
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

# Optional Cursor CLI counterpart of Find-CodexBinary. The check is "is the
# cursor-agent command available": PATH first, then the folder that Cursor's
# install script uses (%LOCALAPPDATA%\cursor-agent). The short command name
# "agent" is not looked up: another program can own it. Only a real executable
# counts: a PowerShell alias or function has no path the agent could start.
# Returns the absolute cursor-agent path, or $null if not found. A miss is not
# warned: Cursor is optional.
function Find-CursorBinary {
    foreach ($name in @('cursor-agent.exe', 'cursor-agent.cmd', 'cursor-agent')) {
        $hit = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $hit) { continue }
        if ($hit.CommandType -ne 'Application') { continue }
        if ($hit.Source -like "*\WindowsApps\*") { continue }
        return $hit.Source
    }
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'cursor-agent\cursor-agent.exe'))
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'cursor-agent\cursor-agent.cmd'))
    }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            return (Resolve-Path -LiteralPath $c).ProviderPath
        }
    }
    return $null
}

# Write MOBAIDE_CURSOR_BIN into an existing agent .env unless the operator
# already set it. It uses set-env, not an init-env argument: set-env takes any
# key, so this installer also works with an agent release that is older than
# Cursor support (an unknown init-env argument would fail the install).
function Set-CursorBinIfMissing {
    param(
        [Parameter(Mandatory=$true)] [string] $EnvFile,
        [Parameter(Mandatory=$true)] [string] $ReleasePython,
        [Parameter(Mandatory=$true)] [string] $CursorBin
    )
    $existing = Read-EnvValue -Path $EnvFile -Key 'MOBAIDE_CURSOR_BIN'
    if ($existing) {
        Write-Info "MOBAIDE_CURSOR_BIN already set in $EnvFile; leaving as-is."
        return
    }
    Write-Info "Writing MOBAIDE_CURSOR_BIN=$CursorBin into $EnvFile"
    & $ReleasePython -m mobaide.agent.config set-env --prefix $Script:Prefix `
        --key MOBAIDE_CURSOR_BIN --value $CursorBin
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "set-env failed (exit $LASTEXITCODE); edit $EnvFile manually and restart $Script:ServiceName."
    }
}

# Optional Junie CLI counterpart of Find-CodexBinary. The check is "is the
# junie command available": PATH first, then the folder that Junie's install
# script uses (%USERPROFILE%\.local\bin, which holds its junie.bat launcher).
# Only a real executable counts: a PowerShell alias or function has no path the
# agent could start. Returns the absolute junie path, or $null if not found. A
# miss is not warned: Junie is optional.
function Find-JunieBinary {
    foreach ($name in @('junie.bat', 'junie.cmd', 'junie.exe', 'junie')) {
        $hit = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $hit) { continue }
        if ($hit.CommandType -ne 'Application') { continue }
        if ($hit.Source -like "*\WindowsApps\*") { continue }
        return $hit.Source
    }
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE '.local\bin\junie.bat'))
        $candidates.Add((Join-Path $env:USERPROFILE '.local\bin\junie.cmd'))
    }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            return (Resolve-Path -LiteralPath $c).ProviderPath
        }
    }
    return $null
}

# Write MOBAIDE_JUNIE_BIN into an existing agent .env unless the operator
# already set it. It uses set-env, not an init-env argument: set-env takes any
# key, so this installer also works with an agent release that is older than
# Junie support (an unknown init-env argument would fail the install).
function Set-JunieBinIfMissing {
    param(
        [Parameter(Mandatory=$true)] [string] $EnvFile,
        [Parameter(Mandatory=$true)] [string] $ReleasePython,
        [Parameter(Mandatory=$true)] [string] $JunieBin
    )
    $existing = Read-EnvValue -Path $EnvFile -Key 'MOBAIDE_JUNIE_BIN'
    if ($existing) {
        Write-Info "MOBAIDE_JUNIE_BIN already set in $EnvFile; leaving as-is."
        return
    }
    Write-Info "Writing MOBAIDE_JUNIE_BIN=$JunieBin into $EnvFile"
    & $ReleasePython -m mobaide.agent.config set-env --prefix $Script:Prefix `
        --key MOBAIDE_JUNIE_BIN --value $JunieBin
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "set-env failed (exit $LASTEXITCODE); edit $EnvFile manually and restart $Script:ServiceName."
    }
}

# Optional Pi coding agent counterpart of Find-CodexBinary. The check is "is
# the pi command available": PATH first, then the two places its installers
# use, the folder of Pi's own installer (%USERPROFILE%\.pi\agent\bin) and the
# npm-global folder (npm install -g @earendil-works/pi-coding-agent). Only a
# real executable counts: a PowerShell alias or function named pi has no path
# the agent could start, and pi.ps1 is a script, not a program. Returns the
# absolute pi path, or $null if not found. A miss is not warned: Pi is optional.
function Find-PiBinary {
    foreach ($name in @('pi.cmd', 'pi.exe')) {
        $hit = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $hit) { continue }
        if ($hit.CommandType -ne 'Application') { continue }
        if ($hit.Source -like "*\WindowsApps\*") { continue }
        return $hit.Source
    }
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE '.pi\agent\bin\pi.cmd'))
    }
    if ($env:APPDATA) {
        $candidates.Add((Join-Path $env:APPDATA 'npm\pi.cmd'))
    }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'npm\pi.cmd'))
    }
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE 'AppData\Roaming\npm\pi.cmd'))
    }
    $candidates.Add('C:\ProgramData\npm\pi.cmd')
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'nodejs\pi.cmd'))
    }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            return (Resolve-Path -LiteralPath $c).ProviderPath
        }
    }
    return $null
}

# Write MOBAIDE_PI_BIN into an existing agent .env unless the operator
# already set it. It uses set-env, not an init-env argument: set-env takes any
# key, so this installer also works with an agent release that is older than
# Pi support (an unknown init-env argument would fail the install).
function Set-PiBinIfMissing {
    param(
        [Parameter(Mandatory=$true)] [string] $EnvFile,
        [Parameter(Mandatory=$true)] [string] $ReleasePython,
        [Parameter(Mandatory=$true)] [string] $PiBin
    )
    $existing = Read-EnvValue -Path $EnvFile -Key 'MOBAIDE_PI_BIN'
    if ($existing) {
        Write-Info "MOBAIDE_PI_BIN already set in $EnvFile; leaving as-is."
        return
    }
    Write-Info "Writing MOBAIDE_PI_BIN=$PiBin into $EnvFile"
    & $ReleasePython -m mobaide.agent.config set-env --prefix $Script:Prefix `
        --key MOBAIDE_PI_BIN --value $PiBin
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

# The agent installs binary packages only, and its dependency lock has them for
# 64-bit Python on x86-64 and ARM64. Keep in sync with LOCK_TARGET_PLATFORMS in
# deploy/prod/release_agent.py. A probe that does not answer lets the install
# go on.
function Assert-SupportedPython {
    param([Parameter(Mandatory=$true)] $PythonExe)
    $probe = @'
import platform, struct
print(platform.machine(), struct.calcsize("P") * 8)
'@
    $build = (Invoke-PythonScript -PythonExe $PythonExe -Script $probe | Out-String).Trim()
    if (-not $build) { return }
    $machine, $bits = $build -split '\s+', 2
    if ($bits -eq '64' -and @('AMD64', 'ARM64', 'x86_64', 'aarch64', 'arm64') -contains $machine) {
        return
    }
    Write-Fail @"
$PythonExe is a $bits-bit Python for $machine. The Mobaide agent needs 64-bit Python 3.12 or newer on an x86-64 or ARM64 computer: it installs prebuilt packages, and they exist only for those.
Install 64-bit Python from https://www.python.org/downloads/windows/, make it the first python on PATH, and run the installer again.
"@
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

# The library that checks the manifest signature, pinned: exact versions and
# the SHA-256 of every file, binary packages only. cryptography 46.0.3 is the
# newest version with binary packages for Intel macOS and for Windows on ARM64;
# its public advisories are in parts that one Ed25519 check does not use. A
# cryptography that Python already has is used as it is (the fast path in
# Resolve-VerifierPython): the pin covers what this script downloads.
# install.sh carries the same list. release_agent.py checks that both match
# and install on every supported platform; --update-verifier-pin rewrites them.
$Script:VerifierRequirements = @'
cffi==2.1.1 ; platform_python_implementation != 'PyPy' \
    --hash=sha256:046bfc24911b37851ee1b51aab8bffe713d89c68c6a057b09484ce9fd5f69b4e \
    --hash=sha256:06c72bb76605a4b0cd0aad6930b69d4baf7dd5d806cfc409b824191099700e66 \
    --hash=sha256:0beceaabe56af686895136a2de78db54ecd8e4046b236b8fd6d6cb61389e9bf2 \
    --hash=sha256:154852545011f779917b11c78db2358d095da62a9a172b78ad0a583ee5adc0d0 \
    --hash=sha256:194cffa889098ced9976c3fc6340305e43f6303657d298da55366907c05c22d6 \
    --hash=sha256:19ee6127ee34de7d83ce3d371ebc5ed91addbdcc39f9ab15ce4eb35a4e534971 \
    --hash=sha256:1a18a57b58cfb21fc28d72e876acf10eaed67a1ed96226f92af4df681d571c4c \
    --hash=sha256:1aa5645c30469b09530c4ebca77ebf8f17618293c58f8549cb1a543a50236e7d \
    --hash=sha256:1dea0e4d7d4f11f619fe8c1d76caf49e24405b4b5743c0e3be16a500ecd930c9 \
    --hash=sha256:208f941bb9d18e768138677f0a6d2ce01f590df56043dda1df1535ac57c88517 \
    --hash=sha256:210019b6c7cf07f081b4c54635c8cf744377001350e29cc0f81c4377b4797735 \
    --hash=sha256:246fa40ce8645a614ff682e0b70f37134e460eaf93a775e0cbe3cca585a67a80 \
    --hash=sha256:25792eac27877609e7bb06d42ff88278a6624fff2ba9bbb523c09616b117e80f \
    --hash=sha256:27350daa11d4f10c540e6e89dada4c54feb7256ad03e9a4dc075ebad7ba360d1 \
    --hash=sha256:28907ab9bfb6aa13184cfc17c6b8e1023c5ab6fd7076d8c20a35e59fe04f8f29 \
    --hash=sha256:2ae64be792b8966f2c69538199728b290e34726562896df1e5dc8ffd8d8188e8 \
    --hash=sha256:31348097ff5bbe827ccc41795d4dd099d9f0625e7def00ee653c137a490c2a6c \
    --hash=sha256:3143d81e29e1e20a9ce10901ec369012947876596f75a222235965f2b7ae832e \
    --hash=sha256:3222ba5d678f80a030e6afbcc33dc1ae5cb45facabb61cee2c7016b8432fde48 \
    --hash=sha256:3311ed60d36f83378794e1009ac6258bafbf81f7888b4caa7b35a521e3f95813 \
    --hash=sha256:334644fbac4eff73d985a17a91226df55d0f394160c4cfb880e084c8f7161cac \
    --hash=sha256:34e261f78cb6ceaaa36f42f2613f4380d94d9c759a9c73c769ee6e0247364632 \
    --hash=sha256:363e05fa78e15116c3c32c210ee36884fd6b9afa6d440e47112c3bd511d64cb6 \
    --hash=sha256:398aff33cee2767e3e781d2554c54bd0dff386bb437581e0d8011fde1a942ec1 \
    --hash=sha256:3d22a20b1fb1632cc72c22f95f7b0d2961c3e1c235f245ba4c606c4771035659 \
    --hash=sha256:42a494cee34437f05546455144f2b5d9ac09b1face62bcfce597d2e521066688 \
    --hash=sha256:42e2f76b9455f5a9a844f770bf3e200ed3da0e15f5df3db9c31fe80b04b3d004 \
    --hash=sha256:42f6930c31dc7f50732c9ae793c2786c7b6b044195967bbdde40bb9be81c4cc0 \
    --hash=sha256:456a61fa52d579ebf9df2e9552ead5129855dbaff6c1e5a9b1bc408809bdc062 \
    --hash=sha256:471cee653ae88de62096552e6d24ccb4a5adb8c8c9f10b5054d0122c15bf2779 \
    --hash=sha256:49cbc70e6542d4ccccb936558d1064a8012541e78f821f955cff24e357776c94 \
    --hash=sha256:4a7c934f7360e8cd64fe9efadcbd10c7c6364f531e432b9a4bf5ccbc9e0e8b50 \
    --hash=sha256:4be96343e422f2dfcd12ab5c9f5aebe03f82f737c6bffeca6830b3875cb44aab \
    --hash=sha256:4f42141fc14250de6dde5ee7ea4432be017252d91f19c5ad043c084cea629cac \
    --hash=sha256:507a24c282e0f42f8ed737cf048572cbf580468da5555764a8331735e9c736b6 \
    --hash=sha256:51b31d1c98274844cfd7838ce00bfc27c7423a4dc00fc0772fc3331c2cc90676 \
    --hash=sha256:58acb8ab8e295e6c5ea12f888cbb13cf21511ef2a3303a23f4325c29d17fe5c1 \
    --hash=sha256:5a59cc1c4442bc3d5c703bf720b51138d0bfc173618807c9ee2490a7541dd3d9 \
    --hash=sha256:5bb4e7ea95dcd6a014a6fef62e62467d67d8e582326443f3d68e71d6320a9fcf \
    --hash=sha256:5c58fe613dc5e5336357eff555824a314d8e43282600435c8d1cb6a7a2fedd13 \
    --hash=sha256:5e7cecbaadb83884793e05828cee59b210b24583b9c7425d0ba6a754fe22eb4e \
    --hash=sha256:616f097f2fe415bc92a247f02e11f634e1f9e9a83d327e3c915c15089c87869e \
    --hash=sha256:63bbfd5ded17c4840ac07cd8f1c21ba9d9708141f840b324f422f41b207e3973 \
    --hash=sha256:64faea20f4e2613363a1a9b9c7dd73058f3ecd00133a511e72ad7c511658f527 \
    --hash=sha256:661c298b4821edebead0c91edd2b00374d67ad7c5a1f7a91d4442633b79d6a72 \
    --hash=sha256:68e62fe11f30d5ca8289242866f0a5291402d8529ca2178ab8afc5c9694ae890 \
    --hash=sha256:6a8dddef476fab96d066d578fc88526767b836ab5ab21754e1d5bf3879c31c7c \
    --hash=sha256:6e192623c49c94421616a5778fba35cf0d5a8d000650c1967ef4448ee5cdd990 \
    --hash=sha256:7225e4514edb64eb6740324353e0da0711954fd8d7da4576755b1c6e09b697cd \
    --hash=sha256:75f80557d1389eddbd0de2681f6a390a0c5338c31ddaa821381c203fc3fd50d9 \
    --hash=sha256:770de9db11e84213beec501cfcaa013b019820ca881e03344dea5844f7876d94 \
    --hash=sha256:7750c6449dff7864bb9bb27ddfb0267756189201a3afc911d82b3caacd70dfc3 \
    --hash=sha256:7bde5e4cc5c10140859842b9d383af292b22639a4dffb725314baf45968cef80 \
    --hash=sha256:7ce713ace7c0e4520535b42b77eaa742c16dab813978064913e5a3cf82973b41 \
    --hash=sha256:7da0c5eff80f0197f3b3d1232ec5a682a9325f4ae9016a78f5f5ca35f9ced1f5 \
    --hash=sha256:7dbb61fe3a7699468030f71bbe5f8a0e326a151daa91beb11a6fc1f980c55e1c \
    --hash=sha256:811bd1e21d32de12efca32393a0ab3f5133b54fce9bd44b8bd77ab07da14bf6a \
    --hash=sha256:8ef53b2de9bcb9197d31854256575d59dbac0cba72ac627bb291ef5eceb74be4 \
    --hash=sha256:937c0052c05a31ca1daf18de3158eed4dbfcb9cc107adbea227728d647be701e \
    --hash=sha256:9d2055050ea716bd38b7f7f1579c275386646b4894c155a3e2f3cd62ed41b7c6 \
    --hash=sha256:9f8d177621de5cb38ee3e731eda45d421db093ec0739f46a5594babda7987a98 \
    --hash=sha256:a2d7755bef5a12ed488f4ef1f1b69ee9191d7396083b755a5d2295f6edb4768b \
    --hash=sha256:a48d62ab9d6f4f98c983223a547af44be6ca3691074c31cecced6facd3ba2dc1 \
    --hash=sha256:a4f00aa42f75d6e4595e8866e748cc1705adc0cddfeb2ca86d0d03993d63ba03 \
    --hash=sha256:a6e721d4b0e45d5b65e87534470e67b18dcd092c83f68fba09f152b9cbc061af \
    --hash=sha256:a730a083190634c65cca36ba5f489531576ebd79bcd5c8e172130f6453127231 \
    --hash=sha256:a931079504ecc49efed7744c476a5c343a92fabf66dec2db95edb1b2fdc770e2 \
    --hash=sha256:aa9511c62d14da7aacc9b4bf51f3f697a621e83b2d6919008243c3aad168eea3 \
    --hash=sha256:ab36d55f9ed2d067327667c2fea18dda018eb628dd6347aa01dda6cf1f5d3836 \
    --hash=sha256:ad2c86c495b899d862ea0f4b42891b8713a3bd45dd4105c7fd51c2a72f39f3a5 \
    --hash=sha256:aeae0e330c9f6acd681f647d46cefd30c29f93e3392882e792e82080c9691399 \
    --hash=sha256:b0431303acaea1089ad4b3e9ce4e6518193def1118d4073ca848635ee4ea2e96 \
    --hash=sha256:b5bdfd1c873d4e093aabc0ca84c4ca6dbc4f752afb5c86f146d9742580c9da2e \
    --hash=sha256:baed1e86cc735622097354b9d1281406caf42ff42a886d29faa8e8d1630333be \
    --hash=sha256:c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf \
    --hash=sha256:c26608d2222fb1e94487e4a387d85f13eb55d5ed725cb25a0c589ac4ee60e7bc \
    --hash=sha256:c7659f22557c5a0bc4855cd635f55edec690cc008a40768527762cb9fb263455 \
    --hash=sha256:c8c69575568085ba0b1b10c0249d779a214aea6f6522e949a0fc9fb0fcb449d0 \
    --hash=sha256:c8d2c9fd1f2d16f780d15127abb050d13d1a76c03a4bd87d7e4980e45e511e12 \
    --hash=sha256:ca82be1a1d406ecfe1d25dc16cb33488e5a16bf4438c9fb590484ea29d92478b \
    --hash=sha256:cc572dace3f60ef98d7b12ff411d20f5362feb31a0439eab0085bbfd349982d7 \
    --hash=sha256:d18e5ac0f2f03f4f518d3e23db0f0cad7faa1da8620e9c09461d443bbf6e6692 \
    --hash=sha256:d28630f5854ab07ab1fd4aba756de52326c82e6be15d414b12793f1975048b54 \
    --hash=sha256:d9c275eaacd24aa73f94ffd6de08fc3f932424d8b6c376f4bed7cde376fe7bc3 \
    --hash=sha256:da0e573f9f97159390c89d9f1a9e41908b66d408cc5b58d08cf3847d844c531b \
    --hash=sha256:dd31f52ea1086513bb9df30f8fcee9b8918323ae067a3d5b78bc826a000712be \
    --hash=sha256:dddad92b554513a31f272570678ba307fb9f618f05e3d4a5eacafff9eae03e1d \
    --hash=sha256:df423d40ee8654634421812bc3b196da3f9bd7d32929da813f8394c4348a5358 \
    --hash=sha256:df913725b79db7bcf03448f36b7bf8815363417d5b58deecf9305e3e30f0f21a \
    --hash=sha256:e0bcb7e0f677f543555d2adff3bf19c05f66cdb4796e5ff602442ab2fe3c4ef7 \
    --hash=sha256:e2d65b31f36619cda3999b78b2aa9632e76b78448e7a56fc4240824200e7c4fc \
    --hash=sha256:e6e8cff14d6fb0be70a09c0bdc58096f501952d04624ebf867e0e56da2df8960 \
    --hash=sha256:f16c709686a78c727bbbf059f92b0bf41c6fc60deec706d2dc19f529175a6125 \
    --hash=sha256:f24fb43132a4c6b4cb4eb029492919b2db645be6808d738f244fd146c03c32cb \
    --hash=sha256:f53e442b08449d42821fa4a4fba000095af9f62742a500f978a9f557ec44339a \
    --hash=sha256:f5cfbc5fe74540d335175b656c725d74d90e3730c626d92575eea35029d9afaa \
    --hash=sha256:f81b3b8f3d4e343550fa4baa0e479bba9f2d29ce9c2e9b51d1ce1718d7442fcf \
    --hash=sha256:f8ec5e643a9a937f64e1999eb9f75d072263751912dc5cd06d3c85f8f44be7c3 \
    --hash=sha256:fb92203a88b3d3053034db775110081c49d28be6551923805e039924093761e4 \
    --hash=sha256:fcd22650c908d7b7da162bbfaab594a1227a15d1643a98c68b122ac642fa2264
cryptography==46.0.3 \
    --hash=sha256:00a5e7e87938e5ff9ff5447ab086a5706a957137e6e433841e9d24f38a065217 \
    --hash=sha256:01ca9ff2885f3acc98c29f1860552e37f6d7c7d013d7334ff2a9de43a449315d \
    --hash=sha256:09859af8466b69bc3c27bdf4f5d84a665e0f7ab5088412e9e2ec49758eca5cbc \
    --hash=sha256:0abf1ffd6e57c67e92af68330d05760b7b7efb243aab8377e583284dbab72c71 \
    --hash=sha256:1000713389b75c449a6e979ffc7dcc8ac90b437048766cef052d4d30b8220971 \
    --hash=sha256:109d4ddfadf17e8e7779c39f9b18111a09efb969a301a31e987416a0191ed93a \
    --hash=sha256:10b01676fc208c3e6feeb25a8b83d81767e8059e1fe86e1dc62d10a3018fa926 \
    --hash=sha256:10ca84c4668d066a9878890047f03546f3ae0a6b8b39b697457b7757aaf18dbc \
    --hash=sha256:15ab9b093e8f09daab0f2159bb7e47532596075139dd74365da52ecc9cb46c5d \
    --hash=sha256:191bb60a7be5e6f54e30ba16fdfae78ad3a342a0599eb4193ba88e3f3d6e185b \
    --hash=sha256:22d7e97932f511d6b0b04f2bfd818d73dcd5928db509460aaf48384778eb6d20 \
    --hash=sha256:23b1a8f26e43f47ceb6d6a43115f33a5a37d57df4ea0ca295b780ae8546e8044 \
    --hash=sha256:36e627112085bb3b81b19fed209c05ce2a52ee8b15d161b7c643a7d5a88491f3 \
    --hash=sha256:39b6755623145ad5eff1dab323f4eae2a32a77a7abef2c5089a04a3d04366715 \
    --hash=sha256:3b51b8ca4f1c6453d8829e1eb7299499ca7f313900dd4d89a24b8b87c0a780d4 \
    --hash=sha256:402b58fc32614f00980b66d6e56a5b4118e6cb362ae8f3fda141ba4689bd4506 \
    --hash=sha256:416260257577718c05135c55958b674000baef9a1c7d9e8f306ec60d71db850f \
    --hash=sha256:46acf53b40ea38f9c6c229599a4a13f0d46a6c3fa9ef19fc1a124d62e338dfa0 \
    --hash=sha256:4b7387121ac7d15e550f5cb4a43aef2559ed759c35df7336c402bb8275ac9683 \
    --hash=sha256:50fc3343ac490c6b08c0cf0d704e881d0d660be923fd3076db3e932007e726e3 \
    --hash=sha256:516ea134e703e9fe26bcd1277a4b59ad30586ea90c365a87781d7887a646fe21 \
    --hash=sha256:549e234ff32571b1f4076ac269fcce7a808d3bf98b76c8dd560e42dbc66d7d91 \
    --hash=sha256:5d7f93296ee28f68447397bf5198428c9aeeab45705a55d53a6343455dcb2c3c \
    --hash=sha256:5ecfccd2329e37e9b7112a888e76d9feca2347f12f37918facbb893d7bb88ee8 \
    --hash=sha256:6276eb85ef938dc035d59b87c8a7dc559a232f954962520137529d77b18ff1df \
    --hash=sha256:6b5063083824e5509fdba180721d55909ffacccc8adbec85268b48439423d78c \
    --hash=sha256:6eae65d4c3d33da080cff9c4ab1f711b15c1d9760809dad6ea763f3812d254cb \
    --hash=sha256:6f61efb26e76c45c4a227835ddeae96d83624fb0d29eb5df5b96e14ed1a0afb7 \
    --hash=sha256:71e842ec9bc7abf543b47cf86b9a743baa95f4677d22baa4c7d5c69e49e9bc04 \
    --hash=sha256:760f83faa07f8b64e9c33fc963d790a2edb24efb479e3520c14a45741cd9b2db \
    --hash=sha256:78a97cf6a8839a48c49271cdcbd5cf37ca2c1d6b7fdd86cc864f302b5e9bf459 \
    --hash=sha256:7ce938a99998ed3c8aa7e7272dca1a610401ede816d36d0693907d863b10d9ea \
    --hash=sha256:8a6e050cb6164d3f830453754094c086ff2d0b2f3a897a1d9820f6139a1f0914 \
    --hash=sha256:9394673a9f4de09e28b5356e7fff97d778f8abad85c9d5ac4a4b7e25a0de7717 \
    --hash=sha256:94cd0549accc38d1494e1f8de71eca837d0509d0d44bf11d158524b0e12cebf9 \
    --hash=sha256:a04bee9ab6a4da801eb9b51f1b708a1b5b5c9eb48c03f74198464c66f0d344ac \
    --hash=sha256:a23582810fedb8c0bc47524558fb6c56aac3fc252cb306072fd2815da2a47c32 \
    --hash=sha256:a2c0cd47381a3229c403062f764160d57d4d175e022c1df84e168c6251a22eec \
    --hash=sha256:a8b17438104fed022ce745b362294d9ce35b4c2e45c1d958ad4a4b019285f4a1 \
    --hash=sha256:a9a3008438615669153eb86b26b61e09993921ebdd75385ddd748702c5adfddb \
    --hash=sha256:b02cf04496f6576afffef5ddd04a0cb7d49cf6be16a9059d793a30b035f6b6ac \
    --hash=sha256:b419ae593c86b87014b9be7396b385491ad7f320bde96826d0dd174459e54665 \
    --hash=sha256:c0a7bb1a68a5d3471880e264621346c48665b3bf1c3759d682fc0864c540bd9e \
    --hash=sha256:c70cc23f12726be8f8bc72e41d5065d77e4515efae3690326764ea1b07845cfb \
    --hash=sha256:c8daeb2d2174beb4575b77482320303f3d39b8e81153da4f0fb08eb5fe86a6c5 \
    --hash=sha256:cb3d760a6117f621261d662bccc8ef5bc32ca673e037c83fbe565324f5c46936 \
    --hash=sha256:d55f3dffadd674514ad19451161118fd010988540cee43d8bc20675e775925de \
    --hash=sha256:d89c3468de4cdc4f08a57e214384d0471911a3830fcdaf7a8cc587e42a866372 \
    --hash=sha256:db391fa7c66df6762ee3f00c95a89e6d428f4d60e7abc8328f4fe155b5ac6e54 \
    --hash=sha256:dfb781ff7eaa91a6f7fd41776ec37c5853c795d3b358d4896fdbb5df168af422 \
    --hash=sha256:e5bf0ed4490068a2e72ac03d786693adeb909981cc596425d09032d372bcc849 \
    --hash=sha256:e7aec276d68421f9574040c26e2a7c3771060bc0cff408bae1dcb19d3ab1e63c \
    --hash=sha256:ef639cb3372f69ec44915fafcd6698b6cc78fbe0c2ea41be867f6ed612811963 \
    --hash=sha256:f260d0d41e9b4da1ed1e0f1ce571f97fe370b152ab18778e9e8f67d6af432018
pycparser==3.12 ; implementation_name != 'PyPy' and platform_python_implementation != 'PyPy' \
    --hash=sha256:a076f3f489b39b43ed13563a1dafe42447445bec2e294154ec5c57fcdda4c85e \
    --hash=sha256:f03387877eab82ce01bbdfd0ba7252ee55a90b1c708d5129e2ce915475cf1db6
'@

# Ephemeral verification venv (Poetry installer pattern). `cryptography`
# ships prebuilt wheels for win_amd64 / win_arm64 on PyPI, so this is a
# fast pip install + a one-shot trust-key / manifest verification, then
# the caller's finally tears the directory down. Deliberately NOT
# `pip install --upgrade pip` first: this venv runs exactly one
# `pip install` and is then deleted, so the upgrade would be a wasted
# network round-trip.
function New-BootstrapVenv {
    param([Parameter(Mandatory=$true)] $PythonExe)
    $root = Join-Path $env:TEMP ("mobaide-verifier-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    try {
        Write-Info "Bootstrapping verification venv (hash-pinned cryptography)..."
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
        #
        # --require-hashes accepts only the files $Script:VerifierRequirements
        # lists; --only-binary refuses a source build, whose build tools no
        # hash covers.
        $verifierRequirementsPath = Join-Path $root "verifier-requirements.txt"
        [System.IO.File]::WriteAllText(
            $verifierRequirementsPath, $Script:VerifierRequirements, [System.Text.UTF8Encoding]::new($false)
        )
        & $venvPip install --isolated --no-cache-dir --disable-pip-version-check --quiet --index-url https://pypi.org/simple --require-hashes --only-binary ":all:" -r $verifierRequirementsPath | Out-Host
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
# public key, so a malformed key surfaces immediately at install time as
# "your key is malformed" rather than later as a confusing
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

# Trust key: the release public key this script carries, unless the operator
# pinned another one with MOBAIDE_AGENT_TRUST_KEY_B64. The key never comes
# from the server that also serves the manifest: that server could then
# sign its own manifest.
function Initialize-TrustKey {
    param($PythonExe)
    if ($Script:TrustKeyB64) {
        Write-Info "Using operator-pinned trust key (MOBAIDE_AGENT_TRUST_KEY_B64)."
    } else {
        $Script:TrustKeyB64 = $Script:ReleasePublicKeyB64
    }
    Confirm-TrustKey -PythonExe $PythonExe
}

function Test-ManifestSignature {
    param($PythonExe, $ManifestPath)
    if (-not $Script:TrustKeyB64) {
        # Initialize-TrustKey should have populated this; defensive only.
        Write-Fail "Internal error: trust key not set before signature verification."
    }
    Write-Info "Verifying manifest signature"
    $verifyScript = @'
import base64, json, sys
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

manifest_path, public_key_b64, minimum = sys.argv[1:4]
record = json.loads(Path(manifest_path).read_text())
body, sig = record["body"], record["signature_b64"]
canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
try:
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64)).verify(
        base64.b64decode(sig), canon
    )
except InvalidSignature:
    sys.exit(
        "manifest signature does not verify. A manifest from your own release "
        "pipeline needs its public key in MOBAIDE_AGENT_TRUST_KEY_B64."
    )


def version_key(value):
    parts = str(value).split(".")
    return tuple(int(p) for p in parts) if all(p.isascii() and p.isdigit() for p in parts) else None


offered = body.get("version")
offered_key = version_key(offered)
if offered_key is None or offered_key < version_key(minimum):
    sys.exit(
        f"the server offers agent version {offered}, but this installer installs "
        f"only version {minimum} or newer. Try again later. If it keeps "
        "failing, download the installer again."
    )
'@
    Invoke-PythonScript -PythonExe $PythonExe -Script $verifyScript -Arguments @($ManifestPath, $Script:TrustKeyB64, $Script:MinimumAgentVersion)
    if ($LASTEXITCODE -ne 0) { Write-Fail "Manifest check failed (signature or minimum agent version); see the reason above." }
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

        # All network I/O happened in PowerShell above; this script only
        # verifies sha256 against the manifest, extracts the tarball, checks
        # the dependency lock from the tarball against the hash the signed
        # manifest names, and writes the hash-pinned requirements file.
        $requirementsPath = Join-Path $tmp.FullName "requirements.txt"
        $stageScript = @'
import hashlib, json, sys, tarfile
from pathlib import Path

manifest_path, tarball_path, wheel_path, release_dir, requirements_path = sys.argv[1:6]
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

lock_sha = str(body.get("dependency_lock_sha256") or "")
if not lock_sha:
    sys.exit(
        "the signed release lists no dependency hashes; this installer installs "
        "only releases that pin every dependency"
    )
lock = Path(release_dir) / "requirements.lock"
if not lock.is_file():
    sys.exit("release tarball has no requirements.lock")
verify_sha256(lock, lock_sha)
Path(requirements_path).write_text(
    lock.read_text(encoding="utf-8")
    + f"\nmobaide-agent @ {Path(wheel_path).resolve().as_uri()}"
    f" --hash=sha256:{body['wheel_sha256'].lower()}\n",
    encoding="utf-8",
)
'@
        Invoke-PythonScript -PythonExe $PythonExe -Script $stageScript -Arguments @($ManifestPath, $tarballPath, $wheelPath, $releaseDir, $requirementsPath) | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Fail "Staging failed" }

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
        # Every dependency at the exact version and SHA-256 that the signed
        # manifest pins. --require-hashes refuses a file with another hash
        # and a dependency the lock does not list; --only-binary refuses
        # source builds, whose build tools no hash covers. pip is the
        # bundled copy.
        Write-Info "Installing the agent with hash-pinned dependencies"
        & $venvPython -m pip install --no-cache-dir --disable-pip-version-check --no-input --require-hashes --only-binary ":all:" -r $requirementsPath | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Dependency install failed: a package did not match the versions and hashes that the signed release lists."
        }
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

    # Cursor CLI counterpart (optional managed agent): the same
    # hint-then-probe discovery and the same soft Info-on-miss.
    $cursorBin = $env:MOBAIDE_AGENT_CURSOR_BIN_HINT
    if (-not $cursorBin) { $cursorBin = Find-CursorBinary }
    if ($cursorBin) {
        Write-Info "Detected Cursor CLI at $cursorBin"
    } else {
        Write-Info "Cursor CLI not detected; leaving MOBAIDE_CURSOR_BIN unset (set it in $Script:Prefix\mobaide_agent\.env if you use Cursor)."
    }

    # Junie CLI counterpart (optional managed agent): the same
    # hint-then-probe discovery and the same soft Info-on-miss.
    $junieBin = $env:MOBAIDE_AGENT_JUNIE_BIN_HINT
    if (-not $junieBin) { $junieBin = Find-JunieBinary }
    if ($junieBin) {
        Write-Info "Detected Junie CLI at $junieBin"
    } else {
        Write-Info "Junie CLI not detected; leaving MOBAIDE_JUNIE_BIN unset (set it in $Script:Prefix\mobaide_agent\.env if you use Junie)."
    }

    # Pi counterpart (optional managed agent): the same
    # hint-then-probe discovery and the same soft Info-on-miss.
    $piBin = $env:MOBAIDE_AGENT_PI_BIN_HINT
    if (-not $piBin) { $piBin = Find-PiBinary }
    if ($piBin) {
        Write-Info "Detected Pi at $piBin"
    } else {
        Write-Info "Pi not detected; leaving MOBAIDE_PI_BIN unset (set it in $Script:Prefix\mobaide_agent\.env if you use Pi)."
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
        if ($cursorBin) {
            Set-CursorBinIfMissing -EnvFile $envFile -ReleasePython $releasePython -CursorBin $cursorBin
        }
        if ($junieBin) {
            Set-JunieBinIfMissing -EnvFile $envFile -ReleasePython $releasePython -JunieBin $junieBin
        }
        if ($piBin) {
            Set-PiBinIfMissing -EnvFile $envFile -ReleasePython $releasePython -PiBin $piBin
        }
        return
    }

    # Existing .env (re-install over a paired agent): leave credentials
    # alone. Surgically backfill MOBAIDE_CLAUDE_BIN / MOBAIDE_CODEX_BIN /
    # MOBAIDE_AGY_BIN only if not already set — an operator's manual edit
    # always wins. This implements the "Surgical set-env only" policy. Each
    # CLI is backfilled independently: a missing Claude path must not skip
    # the Codex or Antigravity backfill (and vice versa). MOBAIDE_COPILOT_BIN
    # MOBAIDE_CURSOR_BIN, MOBAIDE_JUNIE_BIN and MOBAIDE_PI_BIN follow the same
    # rule through Set-CopilotBinIfMissing, Set-CursorBinIfMissing,
    # Set-JunieBinIfMissing and Set-PiBinIfMissing.
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
    if ($cursorBin) {
        Set-CursorBinIfMissing -EnvFile $envFile -ReleasePython $releasePython -CursorBin $cursorBin
    }
    if ($junieBin) {
        Set-JunieBinIfMissing -EnvFile $envFile -ReleasePython $releasePython -JunieBin $junieBin
    }
    if ($piBin) {
        Set-PiBinIfMissing -EnvFile $envFile -ReleasePython $releasePython -PiBin $piBin
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
    # ``port`` defaults to AgentConfig's documented default, 8000. The agent
    # listens on 127.0.0.1.
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
    # is up but can't reach central_server, or knows no relay pairing key (network /
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
(no $pendingFile). The agent likely cannot reach central_server, or the
server's signed release data names no relay pairing key. Check:
    Test-NetConnection -ComputerName api.mobaide.com -Port 443
    Select-String -Path "$Prefix\logs\agent.out.log" -Pattern pairing_monitor.no_pairing_key
    Get-Content "$Prefix\logs\agent.err.log" -Tail 50
The agent is still running; once the cause is fixed it will initiate a
nonce on its own. Run: $($Cli.Command) show-qr
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
    Assert-SupportedPython -PythonExe $python
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
        # Order: choose the trust key (the built-in one, or the operator's) ->
        # fetch manifest -> verify it against that key -> pin the key to disk
        # so the agent picks it up at runtime -> stage. A key that does not
        # verify the manifest never replaces the key of an existing install.
        Initialize-TrustKey -PythonExe $verifier.Python

        $manifest = New-TemporaryFile
        try {
            Get-Manifest -PythonExe $python -ManifestPath $manifest
            Test-ManifestSignature -PythonExe $verifier.Python -ManifestPath $manifest
            Write-TrustKey
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

# Under `irm | iex` this script runs inside the caller's PowerShell, so the
# variable set at the top must not stay in that session.
try {
    Main
} finally {
    Remove-Item Env:\PYTHONSAFEPATH -ErrorAction SilentlyContinue
}
