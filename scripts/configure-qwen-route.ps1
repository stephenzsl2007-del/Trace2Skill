[CmdletBinding()]
param(
    [string]$ProviderName = "qwen-standard",
    [string]$RouteName = "qwen-plus-route",
    [string]$ModelPrefix = "qwen-plus"
)

$ErrorActionPreference = "Stop"
$envPath = Join-Path $env:USERPROFILE "hiclaw-manager.env"
if (-not (Test-Path -LiteralPath $envPath)) {
    throw "AgentTeams environment file not found."
}

$config = @{}
Get-Content -LiteralPath $envPath | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') {
        $config[$Matches[1]] = $Matches[2]
    }
}

foreach ($required in @("HICLAW_ADMIN_USER", "HICLAW_ADMIN_PASSWORD", "HICLAW_LLM_API_KEY")) {
    if ([string]::IsNullOrWhiteSpace($config[$required])) {
        throw "Required AgentTeams setting $required is missing."
    }
}

$gatewayDomain = $config["HICLAW_AI_GATEWAY_DOMAIN"]
if ([string]::IsNullOrWhiteSpace($gatewayDomain)) {
    $gatewayDomain = "aigw-local.hiclaw.io"
}

$consoleUrl = "http://127.0.0.1:18001"
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$loginBody = @{
    username = $config["HICLAW_ADMIN_USER"]
    password = $config["HICLAW_ADMIN_PASSWORD"]
} | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "$consoleUrl/session/login" -Method Post -ContentType "application/json" -Body $loginBody -WebSession $session | Out-Null

$providerBody = [ordered]@{
    type = "qwen"
    name = $ProviderName
    tokens = @($config["HICLAW_LLM_API_KEY"])
    protocol = "openai/v1"
    tokenFailoverConfig = @{ enabled = $false }
    rawConfigs = @{
        qwenEnableSearch = $false
        qwenEnableCompatible = $true
        qwenFileIds = @()
        hiclawMode = $true
    }
}

$existingProvider = $null
try {
    $existingProvider = Invoke-RestMethod -Uri "$consoleUrl/v1/ai/providers/$ProviderName" -WebSession $session
    if ($existingProvider.data) { $existingProvider = $existingProvider.data }
} catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
}

if ($existingProvider) {
    if ($existingProvider.version) { $providerBody.version = $existingProvider.version }
    Invoke-RestMethod -Uri "$consoleUrl/v1/ai/providers/$ProviderName" -Method Put -ContentType "application/json" -Body ($providerBody | ConvertTo-Json -Depth 10 -Compress) -WebSession $session | Out-Null
    Write-Host "Updated native Qwen provider $ProviderName." -ForegroundColor Green
} else {
    Invoke-RestMethod -Uri "$consoleUrl/v1/ai/providers" -Method Post -ContentType "application/json" -Body ($providerBody | ConvertTo-Json -Depth 10 -Compress) -WebSession $session | Out-Null
    Write-Host "Created native Qwen provider $ProviderName." -ForegroundColor Green
}

$routeBody = [ordered]@{
    name = $RouteName
    domains = @($gatewayDomain)
    pathPredicate = @{ matchType = "PRE"; matchValue = "/"; caseSensitive = $false }
    upstreams = @(@{ provider = $ProviderName; weight = 100; modelMapping = @{} })
    modelPredicates = @(@{ matchType = "PRE"; matchValue = $ModelPrefix })
    authConfig = @{
        enabled = $true
        allowedCredentialTypes = @("key-auth")
        allowedConsumers = @("manager")
    }
}

$existingRoute = $null
try {
    $existingRoute = Invoke-RestMethod -Uri "$consoleUrl/v1/ai/routes/$RouteName" -WebSession $session
    if ($existingRoute.data) { $existingRoute = $existingRoute.data }
} catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
}

if ($existingRoute) {
    if ($existingRoute.version) { $routeBody.version = $existingRoute.version }
    Invoke-RestMethod -Uri "$consoleUrl/v1/ai/routes/$RouteName" -Method Put -ContentType "application/json" -Body ($routeBody | ConvertTo-Json -Depth 10 -Compress) -WebSession $session | Out-Null
    Write-Host "Updated model route $RouteName." -ForegroundColor Green
} else {
    Invoke-RestMethod -Uri "$consoleUrl/v1/ai/routes" -Method Post -ContentType "application/json" -Body ($routeBody | ConvertTo-Json -Depth 10 -Compress) -WebSession $session | Out-Null
    Write-Host "Created model route $RouteName." -ForegroundColor Green
}

Write-Host "Higress may need up to 40 seconds to activate the first auth-aware route." -ForegroundColor Cyan
