[CmdletBinding()]
param(
    [switch]$Final
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$materialRoot = Join-Path $repoRoot "submission\preliminary"
$required = @(
    "README.md",
    "01-upload-fields.md",
    "02-work-description.md",
    "03-presentation-content.md",
    "04-attachment-plan.md",
    "05-evidence-index.md",
    "06-submission-checklist.md",
    "material-manifest.json",
    "evidence-summary.json",
    "assets\README.md",
    "final\README.md"
)

foreach ($relative in $required) {
    $path = Join-Path $materialRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing preliminary material: $relative"
    }
}

$descriptionDocument = Get-Content -Raw -LiteralPath (Join-Path $materialRoot "02-work-description.md")
$descriptionMatch = [regex]::Match(
    $descriptionDocument,
    "## 最终提交版\s+(.+?)\s+## 填写说明",
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $descriptionMatch.Success) {
    throw "Unable to find the final work-description block."
}
$description = $descriptionMatch.Groups[1].Value.Trim()
if ($description.Length -gt 500 -or $description.Length -lt 100) {
    throw "Work description must contain 100-500 Unicode characters; actual=$($description.Length)."
}

$manifestPath = Join-Path $materialRoot "material-manifest.json"
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$evidence = Get-Content -Raw -LiteralPath (Join-Path $materialRoot "evidence-summary.json") | ConvertFrom-Json
if ($manifest.fields.work_name.status -ne "ready" -or -not $manifest.fields.work_name.value) {
    throw "Work name is not ready in material-manifest.json."
}
if ($evidence.api_acceptance.status -ne "succeeded") {
    throw "Curated API acceptance evidence is not successful."
}

$evidencePaths = @(
    "docs\trace2skill-mvp-baseline.md",
    "product\evidence\v0.3\api-acceptance\mvp-api-acceptance-01.json",
    "product\evidence\v0.3\experience\analysis.json",
    "product\evidence\v0.3\v1-probes\manifest.json",
    "product\evidence\v0.3\v2-probes-attempt-2\manifest.json"
)
foreach ($relative in $evidencePaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $relative) -PathType Leaf)) {
        throw "Missing source evidence: $relative"
    }
}

$finalRoot = Join-Path $materialRoot "final"
if ($Final) {
    $finalFiles = @(
        "Trace2Skill-初赛方案.pptx",
        "Trace2Skill-初赛方案.pdf",
        "作品简介.txt",
        "Trace2Skill-preliminary-v1.0.zip"
    )
    foreach ($name in $finalFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $finalRoot $name) -PathType Leaf)) {
            throw "Final submission artifact is missing: $name"
        }
    }
    $zip = Get-Item -LiteralPath (Join-Path $finalRoot "Trace2Skill-preliminary-v1.0.zip")
    $zipMb = [math]::Round($zip.Length / 1MB, 2)
    if ($zip.Length -gt 1200MB) {
        throw "Final ZIP exceeds 1200MB: ${zipMb}MB"
    }
    $totalBytes = (Get-ChildItem -LiteralPath $finalRoot -File | Measure-Object -Property Length -Sum).Sum
    if ($totalBytes -gt 3600MB) {
        throw "Final attachment total exceeds 3600MB."
    }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip.FullName).Hash.ToLowerInvariant()
    Write-Output "Final ZIP: ${zipMb}MB sha256=$hash"
}

Write-Output "Preliminary material check passed."
Write-Output "Work name: $($manifest.fields.work_name.value)"
Write-Output "Work description characters: $($description.Length)/500"
Write-Output "Mode: $(if ($Final) { 'final submission' } else { 'preparation package' })"
