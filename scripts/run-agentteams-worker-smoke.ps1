[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $env:USERPROFILE "hiclaw-manager.env"
$outputPath = Join-Path $repoRoot "work\day1-worker-smoke-result.json"

$config = @{}
Get-Content -LiteralPath $envPath | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') { $config[$Matches[1]] = $Matches[2] }
}

$loginBody = @{
    type = "m.login.password"
    identifier = @{ type = "m.id.user"; user = $config["HICLAW_ADMIN_USER"] }
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
if ($managerRooms.Count -ne 1) { throw "Expected exactly one direct-message room with the Manager; found $($managerRooms.Count)." }
$managerRoom = $managerRooms[0]

$sync = Invoke-RestMethod -Uri "$matrixBase/_matrix/client/v3/sync?timeout=0" -Headers $headers -TimeoutSec 10
$since = $sync.next_batch
$sentAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$task = @'
Trace2Skill Day 1 multi-agent smoke task. Use AgentTeams worker-management capabilities to create or reuse a Worker named trace-worker with runtime copaw and model qwen-plus. Delegate this exact read-only task to that Worker: Inspect the text "package manager: npm; failure: ERESOLVE" and identify task_type, package_manager, and first_diagnostic_step. Do not answer the diagnostic task yourself. Wait until the Worker returns its result. Then reply with a final JSON object containing worker_name, runtime, model, delegated, task_type, package_manager, and first_diagnostic_step. Set delegated to true only after receiving the Worker result. Do not modify project files.
'@
$messageBody = @{ msgtype = "m.text"; body = $task.Trim() } | ConvertTo-Json -Compress
$transactionId = "trace2skill-day1-worker-$([Guid]::NewGuid().ToString('n'))"
$encodedManagerRoom = [uri]::EscapeDataString($managerRoom)
$sendUri = "$matrixBase/_matrix/client/v3/rooms/$encodedManagerRoom/send/m.room.message/$transactionId"
$sent = Invoke-RestMethod -Uri $sendUri -Method Put -Headers $headers -ContentType "application/json" -Body $messageBody -TimeoutSec 10

$events = New-Object System.Collections.Generic.List[object]
$finalResponse = $null
$finalEventId = $null
$workerSubmitEventId = $null
$workerCompletionEventId = $null
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline -and -not ($workerSubmitEventId -and $workerCompletionEventId)) {
    $encodedSince = [uri]::EscapeDataString($since)
    $next = Invoke-RestMethod -Uri "$matrixBase/_matrix/client/v3/sync?since=$encodedSince&timeout=10000" -Headers $headers -TimeoutSec 15
    $since = $next.next_batch
    $joinedRooms = $next.rooms.join
    if (-not $joinedRooms) { continue }
    foreach ($roomProperty in @($joinedRooms.PSObject.Properties)) {
        foreach ($event in @($roomProperty.Value.timeline.events)) {
            if ($event.type -ne "m.room.message" -or $event.origin_server_ts -lt $sentAt) { continue }
            $body = [string]$event.content.body
            $sender = [string]$event.sender
            $events.Add([ordered]@{ room_id = $roomProperty.Name; event_id = [string]$event.event_id; sender = $sender; body = $body })
            if ($sender -match '^@trace-worker:' -and $body -match '"ok"\s*:\s*true' -and $body -match '"action"\s*:\s*"submit_task"' -and $body -match '"verified"\s*:\s*true') {
                $workerSubmitEventId = [string]$event.event_id
            }
            if ($sender -match '^@trace-worker:' -and $body -match 'TASK_COMPLETED:\s*trace-task-001') {
                $workerCompletionEventId = [string]$event.event_id
            }
            if ($sender -match '^@manager:' -and $body -match 'trace-worker' -and $body -match '"?delegated"?\s*:\s*true' -and $body -match 'first_diagnostic_step') {
                $finalResponse = $body
                $finalEventId = [string]$event.event_id
            }
        }
    }
}

$passed = [bool]($workerSubmitEventId -and $workerCompletionEventId)
$result = [ordered]@{
    run_type = "day1_agentteams_worker_smoke"
    timestamp = (Get-Date).ToUniversalTime().ToString("o")
    request_event_id = $sent.event_id
    response_event_id = $finalEventId
    worker_submit_event_id = $workerSubmitEventId
    worker_completion_event_id = $workerCompletionEventId
    manager_room_id = $managerRoom
    manager_responded_with_delegation = [bool]$finalResponse
    worker_submitted_verified_result = [bool]$workerSubmitEventId
    worker_reported_completion = [bool]$workerCompletionEventId
    passed = $passed
    task = $task.Trim()
    response = $finalResponse
    observed_events = @($events | ForEach-Object { $_ })
}

$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outputPath -Encoding UTF8
if (-not $passed) { throw "Worker delegation smoke did not pass within $TimeoutSeconds seconds. Evidence: $outputPath" }
Write-Host "AgentTeams Worker delegation smoke passed. Evidence: $outputPath" -ForegroundColor Green
