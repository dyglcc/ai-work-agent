#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

echo "Testing file output modules..."

python -c "from app.services.file_gen import generate_pptx, generate_docx, generate_chart, generate_image; print('[ok] modules imported')"
python - <<'PY'
from app.services.file_gen import generate_pptx, generate_docx, generate_chart

pptx = generate_pptx("test", [{"title": "A", "content": ["1", "2"]}])
print(f"[ok] PPT: {len(pptx)} bytes")

docx = generate_docx("test", [{"heading": "A", "paragraphs": ["1", "2"]}])
print(f"[ok] Word: {len(docx)} bytes")

chart = generate_chart("bar", {"labels": ["A"], "values": [10]}, "test")
print(f"[ok] Chart: {len(chart)} bytes")
PY

echo ""
echo "Start the service with: ./start.sh"
