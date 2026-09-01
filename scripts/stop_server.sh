#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMFYUI_PID_FILE="$PROJECT_ROOT/backend/storage/comfyui.pid"

if pgrep -f 'uvicorn backend.api.main:app' >/dev/null 2>&1; then
  pkill -TERM -f 'uvicorn backend.api.main:app'
  printf '%s\n' "Local video platform server stopped."
else
  printf '%s\n' "Local video platform server is not running."
fi

if [[ -f "$COMFYUI_PID_FILE" ]]; then
  comfyui_pid="$(<"$COMFYUI_PID_FILE")"
  if kill -0 "$comfyui_pid" 2>/dev/null; then
    kill -TERM "$comfyui_pid"
    printf '%s\n' "Managed ComfyUI process stopped."
  fi
  rm -f "$COMFYUI_PID_FILE"
fi
