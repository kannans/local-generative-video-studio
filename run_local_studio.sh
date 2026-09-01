#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFYUI_HOST="${COMFYUI_HOST:-127.0.0.1}"
COMFYUI_PORT="${COMFYUI_PORT:-8188}"
COMFYUI_URL="http://${COMFYUI_HOST}:${COMFYUI_PORT}"
COMFYUI_LOG_FILE="${COMFYUI_LOG_FILE:-$PROJECT_ROOT/backend/storage/comfyui.log}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
backend_pid=""
frontend_pid=""
log_tail_pid=""

is_backend_ready() {
  curl --silent --fail --max-time 2 "$BACKEND_URL/health" >/dev/null 2>&1
}

is_frontend_ready() {
  curl --silent --fail --max-time 2 "$FRONTEND_URL" >/dev/null 2>&1
}

cleanup() {
  trap - INT TERM EXIT
  [[ -n "$backend_pid" ]] && kill "$backend_pid" 2>/dev/null || true
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" 2>/dev/null || true
  [[ -n "$log_tail_pid" ]] && kill "$log_tail_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}

trap cleanup INT TERM EXIT

if ! curl --silent --fail --max-time 2 http://localhost:11434/api/tags >/dev/null; then
  command -v ollama >/dev/null || { printf '%s\n' "Ollama is not installed or unavailable on PATH." >&2; exit 1; }
  ollama serve >/dev/null 2>&1 &
  for _ in {1..15}; do
    curl --silent --fail --max-time 2 http://localhost:11434/api/tags >/dev/null && break
    sleep 1
  done
fi

curl --silent --fail --max-time 2 "$COMFYUI_URL/system_stats" >/dev/null || {
  printf '%s\n' "ComfyUI must be running at $COMFYUI_URL. Start it with scripts/start_server.sh." >&2
  exit 1
}

cd "$PROJECT_ROOT"
if [[ -f "$COMFYUI_LOG_FILE" ]]; then
  printf '%s\n' "Following ComfyUI log: $COMFYUI_LOG_FILE"
  tail -n 0 -F "$COMFYUI_LOG_FILE" &
  log_tail_pid=$!
fi
if is_backend_ready; then
  printf '%s\n' "Backend already running at $BACKEND_URL"
else
  uv run uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 &
  backend_pid=$!
fi
if is_frontend_ready; then
  printf '%s\n' "Frontend already running at $FRONTEND_URL"
else
  (cd frontend && npm run dev -- --port "$FRONTEND_PORT") &
  frontend_pid=$!
fi
printf '%s\n' "Studio: $FRONTEND_URL"
printf '%s\n' "Backend: $BACKEND_URL"
if [[ -n "$backend_pid" ]]; then
  wait "$backend_pid"
elif [[ -n "$frontend_pid" ]]; then
  wait "$frontend_pid"
else
  wait "$log_tail_pid"
fi
