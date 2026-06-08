$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "Checking DingTalk configuration..."
Write-Host ""

if (Test-Path ".env") {
    Write-Host "[ok] .env exists"
} else {
    Write-Host "[warn] .env does not exist"
}

if ((Test-Path ".env") -and (Select-String -Path ".env" -Pattern "^AI_WORK_DINGTALK_ENABLED=true$" -Quiet)) {
    Write-Host "[ok] DingTalk is enabled"
} else {
    Write-Host "[warn] DingTalk is not enabled. Set AI_WORK_DINGTALK_ENABLED=true"
}

$venvActivate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
}

python -c "import dingtalk_stream; print('[ok] dingtalk-stream is installed')"
python -c "from app.platforms.dingtalk.adapter import DingTalkAdapter; print('[ok] DingTalkAdapter can be imported')"

Write-Host ""
Write-Host "Done. Start the service with: .\start.ps1"
