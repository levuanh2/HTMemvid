$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$BeDir = Join-Path $RootDir "BE"

Set-Location $BeDir

$envFile = Join-Path $BeDir ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }
        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) {
            return
        }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

if (-not $env:DATA_DIR) { $env:DATA_DIR = $BeDir }
if (-not $env:PORT) { $env:PORT = "8080" }
if (-not $env:LLM_GATEWAY_PORT) { $env:LLM_GATEWAY_PORT = "50051" }
if (-not $env:MINDMAP_SERVICE_PORT) { $env:MINDMAP_SERVICE_PORT = "50052" }
if (-not $env:LLM_GATEWAY_ADDR) { $env:LLM_GATEWAY_ADDR = "127.0.0.1:$($env:LLM_GATEWAY_PORT)" }
if (-not $env:MINDMAP_SERVICE_ADDR) { $env:MINDMAP_SERVICE_ADDR = "127.0.0.1:$($env:MINDMAP_SERVICE_PORT)" }

$llm = Start-Process python -ArgumentList "-m", "services.llm_gateway.server" -WorkingDirectory $BeDir -PassThru
$mindmap = Start-Process python -ArgumentList "-m", "services.mindmap.server" -WorkingDirectory $BeDir -PassThru

$gunicorn = Get-Command gunicorn -ErrorAction SilentlyContinue
if ($gunicorn) {
    $webConcurrency = if ($env:WEB_CONCURRENCY) { $env:WEB_CONCURRENCY } else { "1" }
    $gunicornTimeout = if ($env:GUNICORN_TIMEOUT) { $env:GUNICORN_TIMEOUT } else { "300" }
    $backend = Start-Process $gunicorn.Source -ArgumentList "-w", $webConcurrency, "-b", "0.0.0.0:$($env:PORT)", "--timeout", $gunicornTimeout, "app.main:app" -WorkingDirectory $BeDir -PassThru
} else {
    $backend = Start-Process python -ArgumentList "-m", "app.main" -WorkingDirectory $BeDir -PassThru
}

Write-Host "llm-gateway pid=$($llm.Id) port=$($env:LLM_GATEWAY_PORT)"
Write-Host "mindmap-service pid=$($mindmap.Id) port=$($env:MINDMAP_SERVICE_PORT)"
Write-Host "backend pid=$($backend.Id) port=$($env:PORT)"
Write-Host "LLM_GATEWAY_ADDR=$($env:LLM_GATEWAY_ADDR)"
Write-Host "MINDMAP_SERVICE_ADDR=$($env:MINDMAP_SERVICE_ADDR)"
