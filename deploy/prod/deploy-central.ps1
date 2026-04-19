<#
    deploy-central.ps1 — Ship central_server to mobaide.com from Windows.

    Usage:
        .\deploy-central.ps1                           # LetsEncrypt TLS, full deploy
        .\deploy-central.ps1 -TlsMode CloudflareOrigin
        .\deploy-central.ps1 -SkipBuild                # restart current image, no source re-sync
        .\deploy-central.ps1 -Rollback                 # swap central_server <-> central_server.prev on server
        .\deploy-central.ps1 -Force                    # allow dirty working tree

    What it does:
      1) Preflight: local tools, clean tree, ssh mobaide reachable, /opt/claude-mobile/.env exists
      2) Tar central_server/ (with excludes) -> scp -> remote atomic swap
      3) scp docker-compose.prod.yml + Caddyfile.<TlsMode> to /opt/claude-mobile/
      4) ssh: docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
      5) Poll https://mobaide.com/api/health for up to 120s
      6) On compose-up or health failure: auto-rollback by swapping central_server.prev back in

    Server-side artifacts (survive across runs):
        /opt/claude-mobile/central_server/         current source
        /opt/claude-mobile/central_server.prev/    previous source (rollback target)
        /opt/claude-mobile/.env                    secrets (NEVER synced)
        /opt/claude-mobile/.last_tag               short git sha of current deploy
        docker volumes: central-server-data, caddy-data, caddy-config
#>

[CmdletBinding()]
param(
    [ValidateSet('LetsEncrypt', 'CloudflareOrigin')]
    [string]$TlsMode = 'LetsEncrypt',

    [switch]$SkipBuild,
    [switch]$Rollback,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$SshHost    = 'mobaide'
$RemoteBase = '/opt/claude-mobile'
$Domain     = 'mobaide.com'
$HealthUrl  = "https://$Domain/api/health"

$DeployDir   = $PSScriptRoot                             # .../deploy/prod/
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $DeployDir)
$SourceDir   = Join-Path $ProjectRoot 'central_server'
$ComposeFile = Join-Path $DeployDir 'docker-compose.prod.yml'
$CaddyFile   = Join-Path $DeployDir "Caddyfile.$TlsMode"

# ------------------------------------------------------------ console helpers
function Write-Step  { param([string]$Msg) Write-Host "`n==> $Msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$Msg) Write-Host "    [OK] $Msg" -ForegroundColor Green }
function Write-Warn2 { param([string]$Msg) Write-Host "    [!!] $Msg" -ForegroundColor Yellow }
function Write-Fail  { param([string]$Msg) Write-Host "    [XX] $Msg" -ForegroundColor Red }

# TLS 1.2+ for Invoke-WebRequest on PS 5.1
[System.Net.ServicePointManager]::SecurityProtocol = `
    [System.Net.SecurityProtocolType]::Tls12 -bor `
    [System.Net.SecurityProtocolType]::Tls11 -bor `
    [System.Net.SecurityProtocolType]::Tls

function Wait-Http {
    param([string]$Url, [int]$TimeoutSec = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $r }
        } catch { Start-Sleep -Seconds 2 }
    }
    throw "Timed out after ${TimeoutSec}s waiting for $Url"
}

function Invoke-Ssh {
    param([Parameter(Mandatory)][string]$Cmd)
    $out = & ssh $SshHost $Cmd 2>&1
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $out }
}

function Invoke-SshOrFail {
    param([Parameter(Mandatory)][string]$Cmd, [Parameter(Mandatory)][string]$FailMsg)
    $r = Invoke-Ssh -Cmd $Cmd
    if ($r.ExitCode -ne 0) {
        Write-Fail "$FailMsg (exit $($r.ExitCode))"
        if ($r.Output) { $r.Output | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
        throw $FailMsg
    }
    return $r.Output
}

# ------------------------------------------------------------ preflight
Write-Step "Preflight"

foreach ($t in 'ssh','scp','tar','git') {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) {
        Write-Fail "$t not found in PATH"
        exit 1
    }
}
Write-Ok "Local tools present: ssh, scp, tar, git"

if (-not (Test-Path $SourceDir))   { Write-Fail "Source dir missing: $SourceDir"; exit 1 }
if (-not (Test-Path $ComposeFile)) { Write-Fail "Compose file missing: $ComposeFile"; exit 1 }
if (-not (Test-Path $CaddyFile))   { Write-Fail "Caddyfile missing: $CaddyFile"; exit 1 }
Write-Ok "Repo artifacts present"

if (-not $Force -and -not $Rollback) {
    Push-Location $ProjectRoot
    try {
        $dirty = & git status --porcelain 2>$null
        if ($LASTEXITCODE -eq 0 -and $dirty) {
            Write-Warn2 "Working tree has uncommitted changes:"
            $dirty | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
            Write-Fail "Refusing to deploy dirty tree. Commit first, or re-run with -Force."
            exit 1
        }
        Write-Ok "Working tree clean"
    } finally { Pop-Location }
}

Invoke-SshOrFail -Cmd "true" `
    -FailMsg "Cannot ssh $SshHost. Check ~/.ssh/config; if it sets 'User root' the server may have PermitRootLogin no -- point at a non-root user that ran bootstrap-mobaide.sh."
$sshUser = (& ssh $SshHost "whoami" 2>$null | Select-Object -Last 1)
if ($sshUser) { $sshUser = $sshUser.Trim() }
if (-not $sshUser) { $sshUser = '<unknown>' }
Write-Ok "ssh $SshHost reachable as '$sshUser'"

Invoke-SshOrFail -Cmd "test -d $RemoteBase" `
    -FailMsg "$RemoteBase does not exist on $SshHost (run deploy/prod/bootstrap-mobaide.sh)"
Invoke-SshOrFail -Cmd "test -w $RemoteBase" `
    -FailMsg "$RemoteBase not writable by '$sshUser'. Bootstrap chowns it to the user who invoked sudo. Either deploy from that account or run: sudo chown -R ${sshUser}: $RemoteBase"
Invoke-SshOrFail -Cmd "test -f $RemoteBase/.env" `
    -FailMsg "$RemoteBase/.env missing on $SshHost (see bootstrap-mobaide.md)"
Invoke-SshOrFail -Cmd "docker info >/dev/null 2>&1" `
    -FailMsg "docker not usable by '$sshUser' on $SshHost. If installed: sudo usermod -aG docker $sshUser, then disconnect & reconnect ssh (group membership is per-session)."
Write-Ok "Server prereqs OK ($RemoteBase, .env, docker, writable by '$sshUser')"

# ------------------------------------------------------------ rollback branch
if ($Rollback) {
    Write-Step "Rollback: swap central_server.prev -> central_server"
    $probe = Invoke-Ssh -Cmd "test -d $RemoteBase/central_server.prev && echo has_prev || echo no_prev"
    if ($probe.Output -notmatch 'has_prev') {
        Write-Fail "No central_server.prev on server; cannot rollback."
        exit 1
    }
    $swap = @"
set -e
cd $RemoteBase
rm -rf central_server.swap
if [ -d central_server ]; then mv central_server central_server.swap; fi
mv central_server.prev central_server
if [ -d central_server.swap ]; then mv central_server.swap central_server.prev; else rm -rf central_server.prev; fi
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
"@
    Invoke-SshOrFail -Cmd $swap -FailMsg "Rollback swap/up failed"
    Write-Ok "Previous version rebuilt and started"
    $null = Wait-Http -Url $HealthUrl -TimeoutSec 120
    Write-Ok "Rollback healthy at $HealthUrl"
    exit 0
}

# ------------------------------------------------------------ tag
Push-Location $ProjectRoot
try { $tag = (& git rev-parse --short HEAD 2>$null).Trim() } finally { Pop-Location }
if (-not $tag) { $tag = (Get-Date).ToString('yyyyMMdd-HHmmss') }
Write-Ok "Deploy tag: $tag"

# ------------------------------------------------------------ sync sources
if (-not $SkipBuild) {
    Write-Step "Packaging central_server/ (tar + excludes)"

    # Sweep stale tarballs (>24h) from past crashed runs without racing a parallel deploy.
    Get-ChildItem $env:TEMP -Filter 'claude-mobile-central-*.tar' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-24) } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    $tarLocal = Join-Path $env:TEMP "claude-mobile-central-$tag.tar"
    if (Test-Path $tarLocal) { Remove-Item $tarLocal -Force }

    $excludeArgs = @(
        '--exclude=__pycache__',
        '--exclude=.pytest_cache',
        '--exclude=.ruff_cache',
        '--exclude=.mypy_cache',
        '--exclude=*.pyc',
        '--exclude=.env',
        '--exclude=.env.*',
        '--exclude=*.db',
        '--exclude=*.db-journal',
        '--exclude=*.db-wal',
        '--exclude=*.db-shm',
        '--exclude=.venv',
        '--exclude=venv'
    )

    try {
        Push-Location $ProjectRoot
        try {
            & tar -cf $tarLocal @excludeArgs central_server
            if ($LASTEXITCODE -ne 0) { Write-Fail "tar failed (exit $LASTEXITCODE)"; exit 1 }
        } finally { Pop-Location }
        $tarBytes = (Get-Item $tarLocal).Length
        Write-Ok ("Archive built: {0} ({1:N0} bytes)" -f $tarLocal, $tarBytes)

        Write-Step "Uploading tarball to $SshHost and extracting atomically"
        Invoke-SshOrFail -Cmd "rm -rf $RemoteBase/central_server.new $RemoteBase/central_server.new.tar && mkdir -p $RemoteBase/central_server.new" `
            -FailMsg "Remote prep failed (check write perms on $RemoteBase as '$sshUser')"

        & scp $tarLocal "${SshHost}:$RemoteBase/central_server.new.tar"
        if ($LASTEXITCODE -ne 0) { Write-Fail "scp tar failed"; exit 1 }

        $extract = @"
set -e
cd $RemoteBase
tar -xf central_server.new.tar -C central_server.new --strip-components=1
rm -f central_server.new.tar
if [ -d central_server ]; then
    rm -rf central_server.prev
    mv central_server central_server.prev
fi
mv central_server.new central_server
echo $tag > .last_tag
"@
        Invoke-SshOrFail -Cmd $extract -FailMsg "Remote extract/swap failed"
        Write-Ok "Source swapped (central_server.prev preserved for rollback)"
    } finally {
        Remove-Item $tarLocal -Force -ErrorAction SilentlyContinue
    }

    Write-Step "Uploading docker-compose.prod.yml and Caddyfile ($TlsMode)"
    & scp $ComposeFile "${SshHost}:$RemoteBase/docker-compose.prod.yml"
    if ($LASTEXITCODE -ne 0) { Write-Fail "scp compose failed"; exit 1 }
    & scp $CaddyFile "${SshHost}:$RemoteBase/Caddyfile"
    if ($LASTEXITCODE -ne 0) { Write-Fail "scp Caddyfile failed"; exit 1 }
    Invoke-SshOrFail -Cmd "mkdir -p $RemoteBase/tls" -FailMsg "mkdir tls failed"
    Write-Ok "compose + Caddyfile uploaded; tls/ ensured"
} else {
    Write-Warn2 "-SkipBuild: reusing existing source on server"
}

# ------------------------------------------------------------ deploy
Write-Step "docker compose up on $SshHost"
$upArgs = if ($SkipBuild) { "up -d --remove-orphans" } else { "up -d --build --remove-orphans" }
$deployCmd = "cd $RemoteBase && docker compose -f docker-compose.prod.yml $upArgs"

& ssh $SshHost $deployCmd   # stream build output live
$composeExit = $LASTEXITCODE

if ($composeExit -ne 0) {
    Write-Fail "docker compose failed (exit $composeExit)"
    if (-not $SkipBuild) {
        Write-Warn2 "Attempting auto-rollback"
        $autoRoll = @"
set -e
cd $RemoteBase
if [ ! -d central_server.prev ]; then echo no_prev; exit 0; fi
rm -rf central_server.swap
mv central_server central_server.swap
mv central_server.prev central_server
mv central_server.swap central_server.prev
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
"@
        $roll = Invoke-Ssh -Cmd $autoRoll
        if ($roll.Output -match 'no_prev') {
            Write-Fail "No previous version to roll back to."
        } elseif ($roll.ExitCode -ne 0) {
            Write-Fail "Auto-rollback also failed. Investigate manually on $SshHost."
        } else {
            Write-Warn2 "Rolled back to previous version."
        }
    }
    exit 1
}
Write-Ok "compose up complete"

# ------------------------------------------------------------ health
Write-Step "Health check: $HealthUrl (up to 120s)"
try {
    $null = Wait-Http -Url $HealthUrl -TimeoutSec 120
    Write-Ok "central-server healthy"
} catch {
    Write-Fail "Health check failed: $_"
    if (-not $SkipBuild) {
        Write-Warn2 "Attempting auto-rollback"
        $autoRoll = @"
set -e
cd $RemoteBase
if [ ! -d central_server.prev ]; then echo no_prev; exit 0; fi
rm -rf central_server.swap
mv central_server central_server.swap
mv central_server.prev central_server
mv central_server.swap central_server.prev
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
"@
        $roll = Invoke-Ssh -Cmd $autoRoll
        if ($roll.Output -match 'no_prev') {
            Write-Fail "No previous version to roll back to."
        } elseif ($roll.ExitCode -ne 0) {
            Write-Fail "Auto-rollback also failed. Investigate manually on $SshHost."
        } else {
            Write-Warn2 "Rolled back. Verify manually: curl $HealthUrl"
        }
    }
    exit 1
}

# ------------------------------------------------------------ summary
Write-Step "Summary"
& ssh $SshHost "cd $RemoteBase && docker compose -f docker-compose.prod.yml ps"
Write-Host ""
Write-Host "Deployed tag : $tag"           -ForegroundColor Cyan
Write-Host "TLS mode     : $TlsMode"       -ForegroundColor Cyan
Write-Host "Health       : $HealthUrl"     -ForegroundColor Cyan
Write-Host "Rollback     : .\deploy-central.ps1 -Rollback" -ForegroundColor Cyan
