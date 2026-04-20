<#
.SYNOPSIS
Switch between named configuration profiles for claude_agent and the Android app.

.DESCRIPTION
Copies (or merges) files from config/profiles/<name>/ into their canonical
locations in the repo. Profiles bundle together every file that varies
between environments (local test stack vs mobaide.com vs ...), so one
command swaps the whole set atomically.

.PARAMETER Name
Name of the profile to apply (must match a directory under config/profiles/).

.PARAMETER List
List available profiles with descriptions.

.PARAMETER Show
Print the currently active profile (from config/.active).

.PARAMETER DryRun
Report planned operations without touching any files.

.PARAMETER Force
Apply without the interactive prompt that appears on first overwrite.

.EXAMPLE
./config/use-profile.ps1 -List

.EXAMPLE
./config/use-profile.ps1 local

.EXAMPLE
./config/use-profile.ps1 mobaide -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Name,

    [switch]$List,
    [switch]$Show,
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$RepoRoot     = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ProfilesRoot = Join-Path $PSScriptRoot 'profiles'
$ActiveFile   = Join-Path $PSScriptRoot '.active'
$TemplateName = '_template'

# --- mapping: profile file (relative to profile dir) -> canonical target (relative to repo root) + mode ---
# Modes:
#   overwrite     : copy profile -> target (create parent dir if needed)
#   merge         : merge only known keys (CENTRAL_SERVER_URL, GOOGLE_WEB_CLIENT_ID) into an existing .properties file
#   copyOrDelete  : copy if present in profile, delete target if absent in profile
$Mappings = @(
    [pscustomobject]@{ Source = 'claude_agent.env';                        Target = 'claude_agent/.env';                                                Mode = 'overwrite' }
    [pscustomobject]@{ Source = 'tunnel.env';                              Target = 'deploy/tunnel.env';                                                Mode = 'overwrite' }
    [pscustomobject]@{ Source = 'android.local.properties';                Target = 'android/local.properties';                                          Mode = 'merge' }
    [pscustomobject]@{ Source = 'android-tls/network_security_config.xml'; Target = 'android/app/src/main/res/xml/network_security_config.xml';          Mode = 'overwrite' }
    [pscustomobject]@{ Source = 'android-tls/caddy_root.crt';              Target = 'android/app/src/main/res/raw/caddy_root.crt';                       Mode = 'copyOrDelete' }
)

$MergeKeys = @('CENTRAL_SERVER_URL', 'GOOGLE_WEB_CLIENT_ID')

# -------- helpers --------

function Get-ProfileDescription {
    param([string]$ProfilePath)
    $metaPath = Join-Path $ProfilePath 'profile.json'
    if (-not (Test-Path -LiteralPath $metaPath)) { return '' }
    try {
        $meta = Get-Content -LiteralPath $metaPath -Raw | ConvertFrom-Json
        return [string]$meta.description
    } catch {
        return ''
    }
}

function Get-AvailableProfiles {
    if (-not (Test-Path -LiteralPath $ProfilesRoot)) { return @() }
    Get-ChildItem -LiteralPath $ProfilesRoot -Directory | Sort-Object Name
}

function Write-Op {
    param([string]$Action, [string]$Target, [string]$Note = '')
    $color = switch ($Action) {
        'create'    { 'Green' }
        'overwrite' { 'Yellow' }
        'merge'     { 'Cyan' }
        'delete'    { 'Red' }
        'skip'      { 'DarkGray' }
        default     { 'Gray' }
    }
    $line = "  [{0,-9}] {1}" -f $Action, $Target
    if ($Note) { $line += "  ($Note)" }
    Write-Host $line -ForegroundColor $color
}

function Merge-LocalProperties {
    <#
    Merge specific keys from $SourceLines into the file at $TargetPath.
    Preserves every line of the target (including comments and sdk.dir).
    Only keys listed in $MergeKeys are touched.
    #>
    param(
        [string[]]$SourceLines,
        [string]$TargetPath,
        [bool]$DryRunMode
    )

    $targetLines = @()
    if (Test-Path -LiteralPath $TargetPath) {
        $targetLines = Get-Content -LiteralPath $TargetPath
    }

    # Parse source for known keys
    $sourceValues = @{}
    foreach ($line in $SourceLines) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $val = $trimmed.Substring($eq + 1)
        if ($MergeKeys -contains $key) {
            $sourceValues[$key] = $val
        } elseif ($trimmed) {
            Write-Warning "Ignoring unknown key '$key' in profile's android.local.properties (only $($MergeKeys -join ', ') are merged)."
        }
    }

    # Build new target lines: replace in place if key exists, track which keys were set
    $seen = @{}
    $newLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $targetLines) {
        $handled = $false
        foreach ($key in $sourceValues.Keys) {
            if ($line -match "^\s*$([regex]::Escape($key))\s*=") {
                $newLines.Add("$key=$($sourceValues[$key])") | Out-Null
                $seen[$key] = $true
                $handled = $true
                break
            }
        }
        if (-not $handled) { $newLines.Add($line) | Out-Null }
    }
    foreach ($key in $sourceValues.Keys) {
        if (-not $seen.ContainsKey($key)) {
            $newLines.Add("$key=$($sourceValues[$key])") | Out-Null
        }
    }

    if ($DryRunMode) { return }

    $parent = Split-Path -Parent $TargetPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    # Use UTF-8 without BOM for .properties (Gradle expects this)
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines($TargetPath, $newLines.ToArray(), $utf8NoBom)
}

function Copy-Overwrite {
    param([string]$SourcePath, [string]$TargetPath, [bool]$DryRunMode)
    if ($DryRunMode) { return }
    $parent = Split-Path -Parent $TargetPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $SourcePath -Destination $TargetPath -Force
}

function Remove-Target {
    param([string]$TargetPath, [bool]$DryRunMode)
    if ($DryRunMode) { return }
    if (Test-Path -LiteralPath $TargetPath) {
        Remove-Item -LiteralPath $TargetPath -Force
    }
}

# -------- subcommands --------

function Invoke-List {
    $profiles = Get-AvailableProfiles
    if (-not $profiles) {
        Write-Host "No profiles found under $ProfilesRoot" -ForegroundColor Yellow
        return
    }
    Write-Host "Available profiles:`n" -ForegroundColor Cyan
    foreach ($p in $profiles) {
        $desc = Get-ProfileDescription -ProfilePath $p.FullName
        if ($p.Name -eq $TemplateName) {
            Write-Host ("  {0,-16} (template) {1}" -f $p.Name, $desc) -ForegroundColor DarkGray
        } else {
            Write-Host ("  {0,-16} {1}" -f $p.Name, $desc)
        }
    }
    Write-Host ''
    Invoke-Show -Silent
}

function Invoke-Show {
    param([switch]$Silent)
    if (Test-Path -LiteralPath $ActiveFile) {
        $content = Get-Content -LiteralPath $ActiveFile -Raw
        if (-not $Silent) { Write-Host "Active profile:" -ForegroundColor Cyan }
        Write-Host $content.TrimEnd()
    } else {
        if (-not $Silent) { Write-Host "No profile has been applied yet." -ForegroundColor DarkGray }
    }
}

function Invoke-Apply {
    param([string]$ProfileName, [bool]$DryRunMode, [bool]$ForceMode)

    if ($ProfileName -eq $TemplateName) {
        throw "Refusing to apply the '_template' profile directly. Copy it to config/profiles/<yourname>/ first."
    }

    $profileDir = Join-Path $ProfilesRoot $ProfileName
    if (-not (Test-Path -LiteralPath $profileDir)) {
        $available = (Get-AvailableProfiles | Where-Object { $_.Name -ne $TemplateName } | ForEach-Object { $_.Name }) -join ', '
        throw "Profile '$ProfileName' not found at $profileDir. Available: $available"
    }

    $desc = Get-ProfileDescription -ProfilePath $profileDir
    $banner = "Applying profile '$ProfileName'"
    if ($DryRunMode) { $banner += " (dry-run)" }
    if ($desc) { $banner += " - $desc" }
    Write-Host $banner -ForegroundColor Cyan

    $plannedOps = New-Object System.Collections.Generic.List[pscustomobject]

    foreach ($m in $Mappings) {
        $srcPath = Join-Path $profileDir $m.Source
        $tgtPath = Join-Path $RepoRoot  $m.Target
        $srcExists = Test-Path -LiteralPath $srcPath
        $tgtExists = Test-Path -LiteralPath $tgtPath

        switch ($m.Mode) {
            'overwrite' {
                if (-not $srcExists) {
                    Write-Op -Action 'skip' -Target $m.Target -Note "not in profile"
                } else {
                    $act = if ($tgtExists) { 'overwrite' } else { 'create' }
                    Write-Op -Action $act -Target $m.Target
                    $plannedOps.Add([pscustomobject]@{ Mode = 'overwrite'; Source = $srcPath; Target = $tgtPath }) | Out-Null
                }
            }
            'merge' {
                if (-not $srcExists) {
                    Write-Op -Action 'skip' -Target $m.Target -Note "not in profile"
                } else {
                    Write-Op -Action 'merge' -Target $m.Target -Note ($MergeKeys -join ', ')
                    $plannedOps.Add([pscustomobject]@{ Mode = 'merge'; Source = $srcPath; Target = $tgtPath }) | Out-Null
                }
            }
            'copyOrDelete' {
                if ($srcExists) {
                    $act = if ($tgtExists) { 'overwrite' } else { 'create' }
                    Write-Op -Action $act -Target $m.Target
                    $plannedOps.Add([pscustomobject]@{ Mode = 'overwrite'; Source = $srcPath; Target = $tgtPath }) | Out-Null
                } elseif ($tgtExists) {
                    Write-Op -Action 'delete' -Target $m.Target -Note "not in profile"
                    $plannedOps.Add([pscustomobject]@{ Mode = 'delete'; Source = $null; Target = $tgtPath }) | Out-Null
                } else {
                    Write-Op -Action 'skip' -Target $m.Target -Note "not in profile, target absent"
                }
            }
        }
    }

    if ($DryRunMode) {
        Write-Host "`nDry-run complete. No files changed." -ForegroundColor DarkGray
        return
    }

    if ($plannedOps.Count -eq 0) {
        Write-Host "`nNothing to do - profile contains no recognized files." -ForegroundColor Yellow
        return
    }

    if (-not $ForceMode) {
        $confirm = Read-Host "`nApply $($plannedOps.Count) operation(s)? [y/N]"
        if ($confirm -notmatch '^[yY]') {
            Write-Host "Aborted." -ForegroundColor Yellow
            return
        }
    }

    foreach ($op in $plannedOps) {
        switch ($op.Mode) {
            'overwrite' { Copy-Overwrite -SourcePath $op.Source -TargetPath $op.Target -DryRunMode $false }
            'merge'     {
                $sourceLines = Get-Content -LiteralPath $op.Source
                Merge-LocalProperties -SourceLines $sourceLines -TargetPath $op.Target -DryRunMode $false
            }
            'delete'    { Remove-Target -TargetPath $op.Target -DryRunMode $false }
        }
    }

    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    @(
        "name=$ProfileName"
        "applied=$timestamp"
    ) | Set-Content -LiteralPath $ActiveFile -Encoding UTF8

    Write-Host "`nProfile '$ProfileName' applied." -ForegroundColor Green
}

# -------- dispatch --------

if ($List)   { Invoke-List;  return }
if ($Show)   { Invoke-Show;  return }

if (-not $Name) {
    Write-Host "Usage:" -ForegroundColor Cyan
    Write-Host "  ./config/use-profile.ps1 <name> [-DryRun] [-Force]"
    Write-Host "  ./config/use-profile.ps1 -List"
    Write-Host "  ./config/use-profile.ps1 -Show"
    Write-Host ""
    Invoke-List
    return
}

Invoke-Apply -ProfileName $Name -DryRunMode:$DryRun.IsPresent -ForceMode:$Force.IsPresent
