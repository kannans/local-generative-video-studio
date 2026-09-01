#!/usr/bin/env bash
set -euo pipefail

if pgrep -f 'uvicorn backend.api.main:app' >/dev/null 2>&1; then
  pkill -TERM -f 'uvicorn backend.api.main:app'
  printf '%s\n' "Local video platform server stopped."
else
  printf '%s\n' "Local video platform server is not running."
fi
