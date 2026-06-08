$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$venvActivate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
}

Write-Host "Testing file output modules..."

python -c "from app.services.file_gen import generate_pptx, generate_docx, generate_chart, generate_image; print('[ok] modules imported')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

@'
from app.services.file_gen import generate_pptx, generate_docx, generate_chart

pptx = generate_pptx("test", [{"title": "A", "content": ["1", "2"]}])
print(f"[ok] PPT: {len(pptx)} bytes")

docx = generate_docx("test", [{"heading": "A", "paragraphs": ["1", "2"]}])
print(f"[ok] Word: {len(docx)} bytes")

chart = generate_chart("bar", {"labels": ["A"], "values": [10]}, "test")
print(f"[ok] Chart: {len(chart)} bytes")
'@ | python -
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Start the service with: .\start.ps1"
