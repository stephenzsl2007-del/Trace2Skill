[CmdletBinding()]
param(
    [string]$Name = "consumer-worker",
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$workers = docker exec hiclaw-controller hiclaw get workers -o json | ConvertFrom-Json
$existing = @($workers.workers | Where-Object { $_.name -eq $Name })
if ($existing.Count -gt 1) {
    throw "More than one AgentTeams Worker is named $Name."
}
if ($existing.Count -eq 0) {
    $identity = "You are the Trace2Skill Skill Consumer Agent. Load only the verified Skill supplied for the current finite task, work only from the isolated task context, return the bounded structured result, and never claim success because the host Validator decides."
    docker exec hiclaw-controller hiclaw create worker `
        --name $Name `
        --runtime copaw `
        --model qwen-plus `
        --identity $identity `
        --wait-timeout "${TimeoutSeconds}s" `
        -o json | Out-Null
}

docker exec hiclaw-controller hiclaw worker ensure-ready --name $Name | Out-Null
$status = docker exec hiclaw-controller hiclaw worker status --name $Name -o json | ConvertFrom-Json
$worker = if ($status.worker) { $status.worker } else { $status }
if ($worker.containerState -ne "running" -or $worker.phase -notin @("Ready", "Running")) {
    throw "AgentTeams consumer Worker is not ready: $Name"
}
$worker | ConvertTo-Json -Depth 6
