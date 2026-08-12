[CmdletBinding()]
param(
    [string]$Model = "qwen-plus",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

$ErrorActionPreference = "Stop"
$agentTeamsVersion = "v1.1.2"
$expectedCommit = "a99457830fafb99c991bdb666aa8a1eef2f83b12"
$repoRoot = Split-Path -Parent $PSScriptRoot
$vendorRoot = Join-Path $repoRoot "work\agentteams-$agentTeamsVersion"

if ([string]::IsNullOrWhiteSpace($env:HICLAW_LLM_API_KEY) -or $env:HICLAW_LLM_API_KEY -eq "<REVOKED-REPLACE-ME>") {
    $secureKey = Read-Host "Alibaba Cloud Bailian API Key" -AsSecureString
    $keyPointer = [IntPtr]::Zero
    try {
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $env:HICLAW_LLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    } finally {
        if ($keyPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
        }
    }

    if ([string]::IsNullOrWhiteSpace($env:HICLAW_LLM_API_KEY)) {
        throw "A Bailian API key is required to install AgentTeams."
    }
}

& (Join-Path $PSScriptRoot "test-agentteams-credential.ps1") -Model $Model -BaseUrl $BaseUrl
if ($LASTEXITCODE -ne 0) {
    $env:HICLAW_LLM_API_KEY = "<REVOKED-REPLACE-ME>"
    throw "API credential preflight failed. Obtain a valid key for the selected endpoint, then rerun this script."
}

$existingAgentTeamsConfig = Test-Path (Join-Path $env:USERPROFILE "hiclaw-manager.env")
& (Join-Path $PSScriptRoot "check-day1.ps1") -AllowExistingAgentTeams:$existingAgentTeamsConfig
if ($LASTEXITCODE -ne 0) {
    throw "Day 1 prerequisites failed. AgentTeams was not installed."
}

if (-not (Test-Path (Join-Path $vendorRoot ".git"))) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $vendorRoot) -Force | Out-Null
    & git -c http.sslBackend=openssl clone --depth 1 --branch $agentTeamsVersion https://github.com/agentscope-ai/AgentTeams.git $vendorRoot
    if ($LASTEXITCODE -ne 0) { throw "Unable to fetch AgentTeams $agentTeamsVersion." }
}

$actualCommit = (& git -C $vendorRoot rev-parse HEAD).Trim()
if ($actualCommit -ne $expectedCommit) {
    throw "AgentTeams source verification failed. Expected $expectedCommit but found $actualCommit."
}

$installer = Join-Path $vendorRoot "install\hiclaw-install.ps1"
if (-not (Test-Path $installer)) {
    throw "Pinned AgentTeams installer was not found at $installer."
}

$env:HICLAW_VERSION = $agentTeamsVersion
$env:HICLAW_NON_INTERACTIVE = "1"
# v1.1.2 keep-all upgrades do not hydrate every config field (notably
# ADMIN_USER) into the installer's in-memory config. Run the confirmation
# steps so environment defaults are materialized before the Manager CR is
# generated. The upstream script asks once for upgrade mode; choose option 2.
$env:HICLAW_UPGRADE_KEEP_ALL = "0"
$env:HICLAW_LLM_PROVIDER = "openai-compat"
$env:HICLAW_DEFAULT_MODEL = $Model
$env:HICLAW_OPENAI_BASE_URL = $BaseUrl
$env:HICLAW_ADMIN_USER = "admin"
$env:HICLAW_WORKSPACE_DIR = Join-Path $repoRoot "work\agentteams-manager"

Write-Host "Installing verified AgentTeams $agentTeamsVersion ($expectedCommit)..." -ForegroundColor Cyan
$previousErrorAction = $ErrorActionPreference
# The upstream installer intentionally probes absent Docker resources and
# handles non-zero exit codes through LASTEXITCODE. PowerShell 5.1 turns those
# probes into terminating errors when the caller inherits Stop.
$ErrorActionPreference = "Continue"
& $installer manager -NonInteractive
$installerExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction

$sanitizer = Join-Path $PSScriptRoot "sanitize-agentteams-log.ps1"
try {
    & $sanitizer
} catch {
    Write-Warning "Could not sanitize the AgentTeams transcript yet; close its PowerShell window and run $sanitizer."
}

if ($installerExitCode -ne 0) {
    throw "AgentTeams installer exited with code $installerExitCode."
}

# QwenPaw keeps the active model in the persistent Manager workspace. An
# in-place AgentTeams upgrade updates the gateway configuration but does not
# replace that selection, so explicitly synchronize it after the services are
# ready.
& (Join-Path $PSScriptRoot "switch-agentteams-model.ps1") -Model $Model
if ($LASTEXITCODE -ne 0) {
    throw "AgentTeams infrastructure is running, but the Manager model could not be synchronized to $Model."
}

Write-Host "AgentTeams installation completed. Open http://127.0.0.1:18088" -ForegroundColor Green
