param(
    [string]$Profile = ""
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$venvActivate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
}

$port = if ($env:AI_WORK_PORT) { $env:AI_WORK_PORT } else { "8000" }
if ($Profile) {
    $env:AI_WORK_ENV = $Profile
}

Write-Host "Starting AI Work Agent..."
if ($env:AI_WORK_ENV) {
    Write-Host "Profile: $env:AI_WORK_ENV"
}
Write-Host "Web UI: http://localhost:$port"
Write-Host "Admin:  http://localhost:$port/admin"
Write-Host "Health: http://localhost:$port/health"
Write-Host ""

python run.py
