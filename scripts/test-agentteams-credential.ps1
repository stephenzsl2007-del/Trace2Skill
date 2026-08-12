[CmdletBinding()]
param(
    [string]$Model = "qwen-plus",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:HICLAW_LLM_API_KEY) -or $env:HICLAW_LLM_API_KEY -eq "<REVOKED-REPLACE-ME>") {
    $secureKey = Read-Host "Alibaba Cloud Model Studio API Key" -AsSecureString
    $keyPointer = [IntPtr]::Zero
    try {
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $env:HICLAW_LLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    } finally {
        if ($keyPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
        }
    }
}
if ([string]::IsNullOrWhiteSpace($env:HICLAW_LLM_API_KEY)) {
    throw "An API key is required."
}

$endpoint = "$($BaseUrl.TrimEnd('/'))/chat/completions"
$payload = @{
    model = $Model
    messages = @(@{ role = "user"; content = "Reply with exactly: OK" })
    max_tokens = 8
    temperature = 0
} | ConvertTo-Json -Depth 5 -Compress

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Net.Http
$client = New-Object System.Net.Http.HttpClient
$request = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Post, $endpoint)
$request.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", $env:HICLAW_LLM_API_KEY)
$request.Content = New-Object System.Net.Http.StringContent($payload, [Text.Encoding]::UTF8, "application/json")

try {
    $response = $client.SendAsync($request).GetAwaiter().GetResult()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) {
        $errorCode = "unknown"
        try {
            $parsed = $body | ConvertFrom-Json
            if ($parsed.error.code) { $errorCode = [string]$parsed.error.code }
        } catch {
            # Do not echo response bodies: gateways may include request details.
        }
        Write-Host "Credential preflight failed: HTTP $([int]$response.StatusCode), code $errorCode." -ForegroundColor Red
        exit 1
    }

    Write-Host "Credential preflight passed for model $Model." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Credential preflight could not reach the configured endpoint: $($_.Exception.GetType().Name)." -ForegroundColor Red
    exit 2
} finally {
    $request.Dispose()
    $client.Dispose()
}
