#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ "${1:-}" != "" ]; then
  export AI_WORK_ENV="$1"
fi

if [ -f ".venv/bin/activate" ]; then
  # macOS/Linux virtualenv
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

echo "Starting AI Work Agent..."
if [ "${AI_WORK_ENV:-}" != "" ]; then
  echo "Profile: ${AI_WORK_ENV}"
fi
echo "Web UI: http://localhost:${AI_WORK_PORT:-8000}"
echo "Admin:  http://localhost:${AI_WORK_PORT:-8000}/admin"
echo "Health: http://localhost:${AI_WORK_PORT:-8000}/health"
echo ""

python run.py
