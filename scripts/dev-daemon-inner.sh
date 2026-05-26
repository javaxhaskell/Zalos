#!/usr/bin/env bash
# Supervisor process started by dev-daemon.sh (nohup). Do not run directly.
set -euo pipefail

ROOT="${ROOT:?}"
NODE_PM="${NODE_PM:-pnpm}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"

cleanup() {
  local pid
  for pid in $(jobs -p); do
    kill -TERM "$pid" 2>/dev/null || true
  done
}
trap cleanup TERM INT EXIT

cd "$ROOT"
set -a
# shellcheck disable=SC1091
source ./.env
set +a

echo "[$(date -Iseconds)] migrate"
make migrate

echo "[$(date -Iseconds)] api :${API_PORT}"
(
  cd "$ROOT/apps/api"
  PYTHONPATH=src uv run uvicorn agentforge.api.main:app \
    --reload \
    --host "${API_HOST:-0.0.0.0}" \
    --port "$API_PORT"
) &

echo "[$(date -Iseconds)] web :${WEB_PORT}"
(
  cd "$ROOT/apps/web"
  "$NODE_PM" run dev:web
) &

wait
