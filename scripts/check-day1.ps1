[CmdletBinding()]
param(
    [switch]$AllowExistingAgentTeams
)

$ErrorActionPreference = "Stop"
$failed = $false

function Write-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail
    )

    $label = if ($Passed) { "PASS" } else { "FAIL" }
    $color = if ($Passed) { "Green" } else { "Red" }
    Write-Host ("[{0}] {1}: {2}" -f $label, $Name, $Detail) -ForegroundColor $color
    if (-not $Passed) { $script:failed = $true }
}

function Write-Warn {
    param([string]$Name, [string]$Detail)
    Write-Host ("[WARN] {0}: {1}" -f $Name, $Detail) -ForegroundColor Yellow
}

Write-Host "Trace2Skill Day 1 environment check" -ForegroundColor Cyan
Write-Host ""

$psOk = $PSVersionTable.PSVersion -ge [Version]"5.1"
Write-Check "PowerShell" $psOk $PSVersionTable.PSVersion.ToString()

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
Write-Check "Docker CLI" ($null -ne $dockerCommand) $(if ($dockerCommand) { $dockerCommand.Source } else { "not found" })

if ($dockerCommand) {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $serverVersion = & docker info --format "{{.ServerVersion}}" 2>$null
    $ErrorActionPreference = $previousErrorAction
    $dockerReady = $LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($serverVersion)
    Write-Check "Docker Engine" $dockerReady $(if ($dockerReady) { "server $serverVersion" } else { "not reachable; start Docker Desktop and wait for Engine running" })
}

$ports = @(18001, 18080, 18088, 18888)
foreach ($port in $ports) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    $available = $null -eq $listener
    if (-not $available -and $AllowExistingAgentTeams) {
        Write-Warn "Port $port" "already in use by the existing AgentTeams installation; allowed for upgrade"
    } else {
        Write-Check "Port $port" $available $(if ($available) { "available" } else { "already in use" })
    }
}

$workspace = Split-Path -Parent $PSScriptRoot
$drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($workspace).TrimEnd("\").TrimEnd(":")) -ErrorAction SilentlyContinue
if ($drive) {
    if ($null -eq $drive.Free -or $drive.Free -eq 0) {
        Write-Warn "Free disk" "not visible from this restricted session; verify 20 GB is available in Windows"
    } else {
        $freeGb = [math]::Round($drive.Free / 1GB, 1)
        Write-Check "Free disk" ($freeGb -ge 20) "$freeGb GB available (20 GB recommended)"
    }
}

Write-Host ""
if ($failed) {
    Write-Host "Day 1 prerequisites are not ready." -ForegroundColor Red
    exit 1
}

Write-Host "Day 1 prerequisites are ready." -ForegroundColor Green
