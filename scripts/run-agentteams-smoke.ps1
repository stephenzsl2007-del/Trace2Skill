[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $env:USERPROFILE "hiclaw-manager.env"
$outputPath = Join-Path $repoRoot "work\day1-smoke-result.json"

if (-not (Test-Path $envPath)) {
    throw "AgentTeams environment file not found at $envPath."
}

$config = @{}
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') {
        $config[$Matches[1]] = $Matches[2]
    }
}

$loginBody = @{
    type = "m.login.password"
    identifier = @{
        type = "m.id.user"
        user = $config["HICLAW_ADMIN_USER"]
    }
    password = $config["HICLAW_ADMIN_PASSWORD"]
} | ConvertTo-Json -Depth 4

$matrixBase = "http://127.0.0.1:18080"
$login = Invoke-RestMethod -Uri "$matrixBase/_matrix/client/v3/login" -Method Post -ContentType "application/json" -Body $loginBody -TimeoutSec 10
$headers = @{ Authorization = "Bearer $($login.access_token)" }
$joined = Invoke-RestMethod -Uri "$matrixBase/_matrix/client/v3/joined_rooms" -Headers $headers -TimeoutSec 10

$managerRooms = New-Object System.Collections.Generic.List[string]
foreach ($roomId in @($joined.joined_rooms)) {
    $encodedRoom = [uri]::EscapeDataString($roomId)
    $members = Invoke-RestMethod -Uri "$matrixBase/_matrix/client/v3/rooms/$encodedRoom/joined_members" -Headers $headers -TimeoutSec 10
    $memberIds = @($members.joined.PSObject.Properties.Name)
    if ($memberIds.Count -eq 2 -and $memberIds -contains $login.user_id -and ($memberIds -match '^@manager:')) {
        $managerRooms.Add($roomId)
    }
}

if ($managerRooms.Count -ne 1) {
    throw "Expected exactly one direct-message room with the Manager; found $($managerRooms.Count)."
}
$managerRoom = $managerRooms[0]

$sync = Invoke-RestMethod -Uri "$matrixBase/_matrix/client/v3/sync?timeout=0" -Headers $headers -TimeoutSec 10
$since = $sync.next_batch
$sentAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$task = 'Smoke task: Inspect the text "package manager: npm; failure: ERESOLVE". Return only a JSON object with string fields task_type, package_manager, and first_diagnostic_step. Do not call tools or modify files.'
$messageBody = @{ msgtype = "m.text"; body = $task } | ConvertTo-Json -Compress
$transactionId = "trace2skill-day1-$([Guid]::NewGuid().ToString('n'))"
$encodedManagerRoom = [uri]::EscapeDataString($managerRoom)
$sendUri = "$matrixBase/_matrix/client/v3/rooms/$encodedManagerRoom/send/m.room.message/$transactionId"
$sent = Invoke-RestMethod -Uri $sendUri -Method Put -Headers $headers -ContentType "application/json" -Body $messageBody -TimeoutSec 10

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$managerResponse = $null
$responseEventId = $null

while ((Get-Date) -lt $deadline -and -not $managerResponse) {
    $encodedSince = [uri]::EscapeDataString($since)
    $next = Invoke-RestMethod -Uri "$matrixBase/_matrix/client/v3/sync?since=$encodedSince&timeout=10000" -Headers $headers -TimeoutSec 15
    $since = $next.next_batch
    $joinedRooms = $next.rooms.join
    if ($joinedRooms) {
        $roomProperty = $joinedRooms.PSObject.Properties[$managerRoom]
        if ($roomProperty) {
            foreach ($event in @($roomProperty.Value.timeline.events)) {
                if ($event.type -eq "m.room.message" -and $event.sender -match '^@manager:' -and $event.origin_server_ts -ge $sentAt) {
                    $managerResponse = [string]$event.content.body
                    $responseEventId = [string]$event.event_id
                    break
                }
            }
        }
    }
}

$passed = $false
if ($managerResponse) {
    $passed = $managerResponse -match 'task_type' -and
        $managerResponse -match 'package_manager' -and
        $managerResponse -match 'first_diagnostic_step' -and
        $managerResponse -match 'npm'
}

$result = [ordered]@{
    run_type = "day1_agentteams_smoke"
    timestamp = (Get-Date).ToUniversalTime().ToString("o")
    request_event_id = $sent.event_id
    response_event_id = $responseEventId
    manager_responded = [bool]$managerResponse
    passed = $passed
    task = $task
    response = $managerResponse
}

New-Item -ItemType Directory -Path (Split-Path -Parent $outputPath) -Force | Out-Null
$result | ConvertTo-Json -Depth 5 | Set-Content -Path $outputPath -Encoding UTF8

if (-not $managerResponse) {
    throw "Manager did not answer the smoke task within $TimeoutSeconds seconds. Result saved to $outputPath."
}
if (-not $passed) {
    throw "Manager answered, but the response did not satisfy the smoke validator. Result saved to $outputPath."
}

Write-Host "AgentTeams smoke task passed. Evidence: $outputPath" -ForegroundColor Green
