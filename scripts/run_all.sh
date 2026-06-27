#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BE_DIR="$ROOT_DIR/BE"

cd "$BE_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

export DATA_DIR="${DATA_DIR:-$BE_DIR}"
export PORT="${PORT:-8080}"
export LLM_GATEWAY_PORT="${LLM_GATEWAY_PORT:-50051}"
export MINDMAP_SERVICE_PORT="${MINDMAP_SERVICE_PORT:-50052}"
export LLM_GATEWAY_ADDR="${LLM_GATEWAY_ADDR:-127.0.0.1:${LLM_GATEWAY_PORT}}"
export MINDMAP_SERVICE_ADDR="${MINDMAP_SERVICE_ADDR:-127.0.0.1:${MINDMAP_SERVICE_PORT}}"

python -m services.llm_gateway.server &
LLM_GATEWAY_PID=$!

python -m services.mindmap.server &
MINDMAP_PID=$!

if command -v gunicorn >/dev/null 2>&1; then
  gunicorn -w "${WEB_CONCURRENCY:-1}" -b "0.0.0.0:${PORT}" --timeout "${GUNICORN_TIMEOUT:-300}" app.main:app &
else
  python -m app.main &
fi
BACKEND_PID=$!

echo "llm-gateway pid=${LLM_GATEWAY_PID} port=${LLM_GATEWAY_PORT}"
echo "mindmap-service pid=${MINDMAP_PID} port=${MINDMAP_SERVICE_PORT}"
echo "backend pid=${BACKEND_PID} port=${PORT}"
echo "LLM_GATEWAY_ADDR=${LLM_GATEWAY_ADDR}"
echo "MINDMAP_SERVICE_ADDR=${MINDMAP_SERVICE_ADDR}"

wait
