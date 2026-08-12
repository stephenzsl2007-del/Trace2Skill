[CmdletBinding()]
param(
    [string]$Model = "qwen-plus",
    [int]$ReadyTimeoutSeconds = 180
)

$ErrorActionPreference = "Continue"
$container = "hiclaw-manager"
$switchScript = "/opt/hiclaw/agent/skills/model-switch/scripts/update-manager-model.sh"
$repoRoot = Split-Path -Parent $PSScriptRoot
$activeModelPath = Join-Path $repoRoot "work\agentteams-manager\.copaw.secret\providers\active_model.json"
$providersPath = Join-Path $repoRoot "work\agentteams-manager\.copaw\providers.json"
$customProviderPath = Join-Path $repoRoot "work\agentteams-manager\.copaw.secret\providers\custom\hiclaw-gateway.json"
$openClawPath = Join-Path $repoRoot "work\agentteams-manager\openclaw.json"

if ($Model -notmatch '^[A-Za-z0-9._-]+$') {
    throw "Model names may contain only letters, digits, dot, underscore, and hyphen."
}

$running = docker ps --format "{{.Names}}" 2>$null | Select-String "^${container}$"
if (-not $running) {
    throw "The $container container is not running."
}

Write-Host "Testing and switching AgentTeams Manager model to $Model..." -ForegroundColor Cyan

# AgentTeams v1.1.2 ships a model-switch script for the previous CoPaw
# single-file secret layout. Current QwenPaw stores the active model and custom
# provider separately. Supply the script with a compatibility copy so its
# gateway preflight and providers update remain authoritative.
docker exec $container sh -c 'if [ ! -f /root/manager-workspace/.copaw.secret/providers.json ]; then cp /root/manager-workspace/.copaw/providers.json /root/manager-workspace/.copaw.secret/providers.json; fi'
if ($LASTEXITCODE -ne 0) {
    throw "Unable to create the QwenPaw provider compatibility view."
}

docker exec $container bash $switchScript $Model --no-reasoning
if ($LASTEXITCODE -ne 0) {
    throw "AgentTeams model preflight or switch failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path $openClawPath)) {
    throw "AgentTeams Manager bootstrap model catalog not found at $openClawPath."
}
$openClaw = Get-Content $openClawPath -Raw | ConvertFrom-Json
$modelKey = "hiclaw-gateway/$Model"
$openClaw.agents.defaults.model.primary = $modelKey
if (-not $openClaw.agents.defaults.models.PSObject.Properties[$modelKey]) {
    $openClaw.agents.defaults.models | Add-Member -NotePropertyName $modelKey -NotePropertyValue ([pscustomobject]@{ alias = $Model })
}
$bootstrapGateway = $openClaw.models.providers.'hiclaw-gateway'
$bootstrapModels = @($bootstrapGateway.models | ForEach-Object { $_.id })
if ($bootstrapModels -notcontains $Model) {
    $bootstrapGateway.models = @($bootstrapGateway.models) + @([pscustomobject]@{
        contextWindow = 150000
        id = $Model
        input = @("text")
        maxTokens = 128000
        name = $Model
        reasoning = $false
    })
}
$openClawJson = $openClaw | ConvertTo-Json -Depth 30
[System.IO.File]::WriteAllText($openClawPath, $openClawJson, (New-Object System.Text.UTF8Encoding $false))

if (-not (Test-Path $customProviderPath)) {
    throw "QwenPaw authoritative gateway provider file not found at $customProviderPath."
}
$customProvider = Get-Content $customProviderPath -Raw | ConvertFrom-Json
$customKnownModels = @($customProvider.extra_models | ForEach-Object { $_.id })
if ($customKnownModels -notcontains $Model) {
    $customProvider.extra_models = @($customProvider.extra_models) + @([pscustomobject]@{
        id = $Model
        name = $Model
        supports_multimodal = $null
        supports_image = $null
        supports_video = $null
        probe_source = $null
        generate_kwargs = [pscustomobject]@{}
    })
}
$customProviderJson = $customProvider | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($customProviderPath, $customProviderJson, (New-Object System.Text.UTF8Encoding $false))

if (-not (Test-Path $providersPath)) {
    throw "QwenPaw provider catalog not found at $providersPath."
}
$providers = Get-Content $providersPath -Raw | ConvertFrom-Json
$gatewayProvider = $providers.custom_providers.'hiclaw-gateway'
if (-not $gatewayProvider) {
    throw "QwenPaw hiclaw-gateway provider is missing from the provider catalog."
}
$knownModels = @($gatewayProvider.models | ForEach-Object { $_.id })
if ($knownModels -notcontains $Model) {
    $gatewayProvider.models = @($gatewayProvider.models) + @([pscustomobject]@{ id = $Model; name = $Model })
}
$providers.active_llm.provider_id = "hiclaw-gateway"
$providers.active_llm.model = $Model
$providersJson = $providers | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($providersPath, $providersJson, (New-Object System.Text.UTF8Encoding $false))

if (-not (Test-Path $activeModelPath)) {
    throw "QwenPaw active model file not found at $activeModelPath."
}
$activeModel = Get-Content $activeModelPath -Raw | ConvertFrom-Json
$activeModel.model = $Model
$activeModelJson = $activeModel | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($activeModelPath, $activeModelJson, (New-Object System.Text.UTF8Encoding $false))

docker restart $container | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to restart $container after the model switch."
}

$deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $result = docker exec $container curl -sf http://127.0.0.1:18799/api/agents 2>$null
    $ErrorActionPreference = $previousErrorAction
    if ($LASTEXITCODE -eq 0 -and $result -match '"agents"') {
        Write-Host "AgentTeams Manager is ready with model $Model." -ForegroundColor Green
        exit 0
    }
    Start-Sleep -Seconds 5
}

throw "Manager did not become ready within $ReadyTimeoutSeconds seconds after the model switch."
