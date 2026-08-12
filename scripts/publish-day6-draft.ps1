[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Profile = "default",
    [string]$RegistryHost = "market.hiclaw.io",
    [int]$RegistryPort = 80,
    [string]$Namespace = "public"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Name = "diagnose-javascript-dependency-failures"
$Version = "2.0.0"
$Bundle = Join-Path $ProjectRoot "release-bundles\$Name\$Version\$Name-$Version.zip"
$Manifest = Join-Path $ProjectRoot "release-bundles\$Name\$Version\release-manifest.json"
$ContainerPath = "/tmp/trace2skill-release/$Name-$Version.zip"

if (-not (Test-Path -LiteralPath $Bundle -PathType Leaf) -or -not (Test-Path -LiteralPath $Manifest -PathType Leaf)) {
    throw "Release bundle is missing. Run: python .\scripts\build_day6_release.py"
}

$Release = Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json
$ActualHash = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualHash -ne $Release.package.sha256) {
    throw "Release ZIP hash does not match release-manifest.json"
}
if ($Release.registry.state -ne "staged-local" -or $Release.registry.external_write_performed -ne $false) {
    throw "Release manifest is not in the expected local staging state"
}

Write-Host "Validated local bundle: $Bundle"
Write-Host "Target: nacos://$RegistryHost`:$RegistryPort/$Namespace"
Write-Host "Action: upload one editing draft only; review and release remain separate human-governed steps."

if (-not $Apply) {
    Write-Host "DRY RUN: no registry write performed. Re-run with -Apply after configuring the scoped '$Profile' profile inside trace-worker."
    exit 0
}

docker exec hiclaw-worker-trace-worker mkdir -p /tmp/trace2skill-release
if ($LASTEXITCODE -ne 0) { throw "Unable to prepare the Worker staging directory" }
docker cp $Bundle "hiclaw-worker-trace-worker:$ContainerPath"
if ($LASTEXITCODE -ne 0) { throw "Unable to copy the release bundle into trace-worker" }
docker exec hiclaw-worker-trace-worker nacos-cli --profile $Profile --host $RegistryHost --port $RegistryPort --namespace $Namespace skill-upload $ContainerPath
if ($LASTEXITCODE -ne 0) { throw "Nacos draft upload failed" }

Write-Host "Draft uploaded. Do not call skill-review or skill-release until the returned version and pipeline policy are reviewed."
