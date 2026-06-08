#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "Checking DingTalk configuration..."
echo ""

if [ -f ".env" ]; then
  echo "[ok] .env exists"
else
  echo "[warn] .env does not exist"
fi

if [ -f ".env" ] && grep -q "AI_WORK_DINGTALK_ENABLED=true" ".env"; then
  echo "[ok] DingTalk is enabled"
else
  echo "[warn] DingTalk is not enabled. Set AI_WORK_DINGTALK_ENABLED=true"
fi

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

python -c "import dingtalk_stream; print('[ok] dingtalk-stream is installed')"
python -c "from app.platforms.dingtalk.adapter import DingTalkAdapter; print('[ok] DingTalkAdapter can be imported')"

echo ""
echo "Done. Start the service with: ./start.sh"
