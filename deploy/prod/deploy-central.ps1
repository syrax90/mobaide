<#
    deploy-central.ps1 — Ship central_server to mobaide.com from Windows.

    Usage:
        .\deploy-central.ps1                           # LetsEncrypt TLS, full deploy
        .\deploy-central.ps1 -TlsMode CloudflareOrigin
        .\deploy-central.ps1 -SkipBuild                # restart current image, no source re-sync
        .\deploy-central.ps1 -Rollback                 # swap central_server <-> central_server.prev on server
        .\deploy-central.ps1 -Force                    # allow dirty working tree

    What it does:
      1) Preflight: local tools, clean tree, ssh mobaide reachable, /opt/mobaide/.env exists
      2) Tar central_server/ (with excludes) -> scp -> remote atomic swap
      3) scp docker-compose.prod.yml + Caddyfile.<TlsMode> to /opt/mobaide/
      4) ssh: docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
      5) Poll https://mobaide.com/api/health for up to 120s
      6) On compose-up or health failure: auto-rollback by swapping central_server.prev back in

    Server-side artifacts (survive across runs):
        /opt/mobaide/central_server/         current source
        /opt/mobaide/central_server.prev/    previous source (rollback target)
        /opt/mobaide/.env                    secrets (NEVER synced)
        /opt/mobaide/.last_tag               short git sha of current deploy
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
$RemoteBase = '/opt/mobaide'
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
    # Ship the script as base64 inside a single-word ssh argument, then decode
    # and pipe into bash on the server. Two Windows-specific problems vanish:
    #   (1) CreateProcess wraps multi-word args in "..." without escaping
    #       embedded " chars, so `echo "USER=$u"` reaches bash with a broken
    #       quoting state -> `unexpected EOF while looking for matching "`.
    #   (2) PowerShell's `| native.exe` re-inserts CRLF on Windows, so piping
    #       the script to `bash -s` leaves \r at EOL and breaks args like
    #       `head -5` (becomes `head -5\r`, "invalid trailing option").
    # Base64 is printable ASCII with no shell metachars, no " and no newlines,
    # so it survives both stages untouched.
    $normalized = $Cmd -replace "`r`n", "`n"
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($normalized))
    $out = & ssh $SshHost "echo $b64 | base64 -d | bash" 2>&1
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $out }
}

function Invoke-SshOrFail {
    param([Parameter(Mandatory)][string]$Cmd, [Parameter(Mandatory)][string]$FailMsg)
    $r = Invoke-Ssh -Cmd $Cmd
    if ($r.ExitCode -ne 0) {
        Write-Fail "$FailMsg (exit $($r.ExitCode))"
        if ($r.Output) { $r.Output | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
        $joined = ($r.Output | Out-String)
        if ($joined -match '(?i)Connection timed out|Connection refused|port 22') {
            Write-Host "      Likely UFW rate-limit on $SshHost (server drops after 6 SSH connections in 30s)." -ForegroundColor DarkGray
            Write-Host "      Wait 30s and retry. Persistent? Check ufw / fail2ban on the server." -ForegroundColor DarkGray
        }
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

# No ssh multiplexing: Git's MSYS2 ssh and Windows OpenSSH disagree on
# ControlPath syntax (Unix socket file vs `\\.\pipe\...`), and which one wins
# depends on $env:PATH order. Rather than picking sides, run each call with
# defaults and keep the total connection count low by batching server-side —
# the server has `ufw limit 22/tcp`, which silently drops SYNs past 6
# connections in 30s, so every saved round-trip matters.

# Batched preflight: one SSH round-trip exercises the transport, reports the
# remote user, and flags every failing prereq at once — so the operator fixes
# them in a single pass instead of discovering them one connection at a time.
# If ssh fails to even reach the remote bash, we surface that as a separate
# reachability error (see the `-not $errs` branch below).
$preflightScript = @"
set +e
user=`$(whoami)
echo "USER=`$user"
errs=""
[ -d $RemoteBase ]          || errs="`$errs BASE_MISSING"
[ -w $RemoteBase ]          || errs="`$errs BASE_NOT_WRITABLE"
[ -f $RemoteBase/.env ]     || errs="`$errs ENV_MISSING"
docker info >/dev/null 2>&1 || errs="`$errs DOCKER"
if [ -n "`$errs" ]; then echo "ERRORS:`$errs"; exit 1; fi
echo "OK"
"@

$probe = Invoke-Ssh -Cmd $preflightScript
$userLine = $probe.Output | Select-String '^USER=' | Select-Object -First 1
$sshUser = if ($userLine) { ($userLine.Line -replace '^USER=','').Trim() } else { '<unknown>' }
if (-not $sshUser) { $sshUser = '<unknown>' }

if ($probe.ExitCode -ne 0) {
    $errLine = $probe.Output | Select-String '^ERRORS:' | Select-Object -First 1
    $errs = if ($errLine) { ($errLine.Line -replace '^ERRORS:','').Trim() } else { '' }
    if (-not $errs) {
        # Probe never reached remote bash — SSH transport failure.
        Write-Fail "Cannot ssh $SshHost. Raw error:"
        $probe.Output | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        Write-Host "      Common causes:" -ForegroundColor DarkGray
        Write-Host "      - Host '$SshHost' missing or misconfigured in ~/.ssh/config" -ForegroundColor DarkGray
        Write-Host "      - Key auth failing (IdentityFile path / agent / server ~/.ssh/authorized_keys)" -ForegroundColor DarkGray
        Write-Host "      - UFW rate-limit tripped: server drops after 6 SSH connections in 30s" -ForegroundColor DarkGray
        Write-Host "      - Network / sshd unreachable (reboot, firewall)" -ForegroundColor DarkGray
        Write-Host "      - 'User root' in ssh config but server has PermitRootLogin no (use the bootstrap user)" -ForegroundColor DarkGray
    } else {
        if ($errs -match 'BASE_MISSING')      { Write-Fail "$RemoteBase does not exist on $SshHost (run deploy/prod/bootstrap-mobaide.sh)" }
        if ($errs -match 'BASE_NOT_WRITABLE') { Write-Fail "$RemoteBase not writable by '$sshUser'. Bootstrap chowns it to the user who invoked sudo. Either deploy from that account or run: sudo chown -R ${sshUser}: $RemoteBase" }
        if ($errs -match 'ENV_MISSING')       { Write-Fail "$RemoteBase/.env missing on $SshHost (see bootstrap-mobaide.md)" }
        if ($errs -match 'DOCKER')            { Write-Fail "docker not usable by '$sshUser' on $SshHost. If installed: sudo usermod -aG docker $sshUser, then disconnect & reconnect ssh (group membership is per-session)." }
    }
    exit 1
}

Write-Ok "ssh $SshHost reachable as '$sshUser'"
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
    Get-ChildItem $env:TEMP -Filter 'mobaide-central-*.tar' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-24) } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    $tarLocal = Join-Path $env:TEMP "mobaide-central-$tag.tar"
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
        # scp writes to a file path, not a directory, so it only needs $RemoteBase
        # to exist (already verified by preflight). All staging prep (rm old
        # central_server.new/, mkdir central_server.new + tls) is folded into the
        # extract heredoc below to save two SSH round-trips under ufw's 6/30s limit.
        & scp $tarLocal "${SshHost}:$RemoteBase/central_server.new.tar"
        if ($LASTEXITCODE -ne 0) { Write-Fail "scp tar failed"; exit 1 }

        $extract = @"
set -e
cd $RemoteBase
rm -rf central_server.new
mkdir -p central_server.new tls
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
    # Single scp with two sources, trailing slash on dest => both files land in
    # $RemoteBase/ with their local names. Caddyfile arrives as Caddyfile.$TlsMode;
    # the deployCmd below copies it to `Caddyfile` (the name docker-compose mounts)
    # just before `docker compose up`. tls/ is created by the $extract heredoc.
    & scp $ComposeFile $CaddyFile "${SshHost}:$RemoteBase/"
    if ($LASTEXITCODE -ne 0) { Write-Fail "scp compose+Caddyfile failed"; exit 1 }
    Write-Ok "compose + Caddyfile.$TlsMode uploaded"
} else {
    Write-Warn2 "-SkipBuild: reusing existing source on server"
}

# ------------------------------------------------------------ deploy
Write-Step "docker compose up on $SshHost"
$upArgs = if ($SkipBuild) { "up -d --remove-orphans" } else { "up -d --build --remove-orphans" }
# One SSH round-trip does: (a) copy Caddyfile.$TlsMode -> Caddyfile so docker-compose
# can mount it, (b) compose up (streaming), (c) compose ps (replaces the ex-line-358
# summary call). Strict `cp -f`: if Caddyfile.$TlsMode is missing (e.g. -SkipBuild
# on a never-deployed $TlsMode), the deploy fails loudly instead of silently
# running the wrong TLS mode against a stale Caddyfile.
$deployCmd = "cd $RemoteBase && cp -f Caddyfile.$TlsMode Caddyfile && docker compose -f docker-compose.prod.yml $upArgs && docker compose -f docker-compose.prod.yml ps"

& ssh $SshHost ($deployCmd -replace "`r`n", "`n")   # stream build output live
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
# `docker compose ps` was already streamed at the tail of $deployCmd above,
# so the summary section is local-only (no SSH round-trip).
Write-Step "Summary"
Write-Host "Deployed tag : $tag"           -ForegroundColor Cyan
Write-Host "TLS mode     : $TlsMode"       -ForegroundColor Cyan
Write-Host "Health       : $HealthUrl"     -ForegroundColor Cyan
Write-Host "Rollback     : .\deploy-central.ps1 -Rollback" -ForegroundColor Cyan
