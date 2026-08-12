[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$outputPath = Join-Path $repoRoot "work\agentteams-diagnostics.txt"
$sections = New-Object System.Collections.Generic.List[string]

function Protect-Text {
    param([string]$Text)

    if ($null -eq $Text) { return "" }
    $protected = $Text -replace 'sk-[A-Za-z0-9_-]{10,}', '<redacted-api-key>'
    $protected = $protected -replace '(?i)(api[_ -]?key|token|password|secret)(["''\s:=]+)([^,\s"'']+)', '$1$2<redacted>'
    return $protected
}

function Add-Section {
    param([string]$Title, [scriptblock]$Command)

    $sections.Add("=== $Title ===")
    try {
        $content = & $Command 2>&1 | Out-String
        $sections.Add((Protect-Text $content.TrimEnd()))
    } catch {
        $sections.Add((Protect-Text $_.Exception.Message))
    }
    $sections.Add("")
}

New-Item -ItemType Directory -Path (Split-Path -Parent $outputPath) -Force | Out-Null

Add-Section "timestamp" { Get-Date -Format o }
Add-Section "docker server" { docker info --format 'server={{.ServerVersion}}' }
Add-Section "hiclaw containers" { docker ps -a --filter 'name=hiclaw' --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' }
Add-Section "hiclaw network" { docker network inspect hiclaw-net --format '{{json .Containers}}' }
Add-Section "controller state" { docker inspect hiclaw-controller --format 'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} exit={{.State.ExitCode}} error={{.State.Error}}' }
Add-Section "controller logs" { docker logs --tail 300 hiclaw-controller }
Add-Section "controller error file" { docker exec hiclaw-controller sh -c 'tail -200 /var/log/hiclaw/hiclaw-controller-error.log 2>/dev/null || true' }
Add-Section "controller manager resource" { docker exec hiclaw-controller hiclaw get managers default -o json }

[System.IO.File]::WriteAllLines($outputPath, $sections, (New-Object System.Text.UTF8Encoding $false))
Write-Host "Sanitized diagnostics written to $outputPath" -ForegroundColor Green
