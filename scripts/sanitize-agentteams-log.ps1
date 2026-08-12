[CmdletBinding()]
param(
    [string]$Path = (Join-Path $env:USERPROFILE "hiclaw-install.log")
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Path)) { return }

$lines = Get-Content -LiteralPath $Path
$clean = $lines | ForEach-Object {
    $line = $_
    $line = $line -replace 'sk-[A-Za-z0-9_-]{10,}', '<redacted-api-key>'
    if ($line -match '\$env:HICLAW_LLM_API_KEY\s*=') {
        return '$env:HICLAW_LLM_API_KEY = "<redacted>"'
    }
    if ($line -match '^HICLAW_LLM_API_KEY=') {
        return 'HICLAW_LLM_API_KEY=<redacted>'
    }
    $line = $line -replace '(?i)(password|密码)(\s*[:=]\s*)([^\s，,)]+)', '$1$2<redacted>'
    $line = $line -replace '(?i)(password|密码)(\s*[:=]\s*)([^\r\n]+)$', '$1$2<redacted>'
    return $line
}

Set-Content -LiteralPath $Path -Value $clean -Encoding UTF8
Write-Host "Sanitized AgentTeams install log: $Path" -ForegroundColor Green
