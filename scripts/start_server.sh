#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMFYUI_DIR="${COMFYUI_DIR:-/Users/kannan.s/projects/AIML/ComfyUI}"
COMFYUI_PYTHON="${COMFYUI_PYTHON:-$COMFYUI_DIR/.venv/bin/python}"
COMFYUI_HOST="${COMFYUI_HOST:-127.0.0.1}"
COMFYUI_PORT="${COMFYUI_PORT:-8188}"
COMFYUI_URL="http://${COMFYUI_HOST}:${COMFYUI_PORT}"
COMFYUI_PID_FILE="$PROJECT_ROOT/backend/storage/comfyui.pid"
COMFYUI_LOG_FILE="$PROJECT_ROOT/backend/storage/comfyui.log"
FRONTEND_DIR="${FRONTEND_DIR:-$PROJECT_ROOT/frontend}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
FRONTEND_URL="http://${FRONTEND_HOST}:${FRONTEND_PORT}"
FRONTEND_PID_FILE="$PROJECT_ROOT/backend/storage/frontend.pid"
FRONTEND_LOG_FILE="$PROJECT_ROOT/backend/storage/frontend.log"

is_comfyui_ready() {
	curl --silent --fail --max-time 2 "$COMFYUI_URL/system_stats" >/dev/null 2>&1
}

start_comfyui() {
	if is_comfyui_ready; then
		printf '%s\n' "ComfyUI is already running at $COMFYUI_URL."
		return
	fi

	if [[ ! -f "$COMFYUI_DIR/main.py" ]]; then
		printf '%s\n' "ComfyUI was not found at $COMFYUI_DIR." >&2
		printf '%s\n' "Set COMFYUI_DIR to the directory containing ComfyUI's main.py." >&2
		exit 1
	fi

	mkdir -p "$(dirname "$COMFYUI_PID_FILE")"
	rm -f "$COMFYUI_PID_FILE"
	printf '%s\n' "Starting ComfyUI at $COMFYUI_URL..."
	(
		cd "$COMFYUI_DIR"
		exec "$COMFYUI_PYTHON" main.py --listen "$COMFYUI_HOST" --port "$COMFYUI_PORT"
	) >"$COMFYUI_LOG_FILE" 2>&1 &
	local comfyui_pid=$!
	printf '%s\n' "$comfyui_pid" >"$COMFYUI_PID_FILE"

	for _ in {1..60}; do
		if is_comfyui_ready; then
			printf '%s\n' "ComfyUI is ready."
			return
		fi
		if ! kill -0 "$comfyui_pid" 2>/dev/null; then
			rm -f "$COMFYUI_PID_FILE"
			printf '%s\n' "ComfyUI stopped during startup. See $COMFYUI_LOG_FILE." >&2
			exit 1
		fi
		sleep 1
	done

	kill -TERM "$comfyui_pid" 2>/dev/null || true
	rm -f "$COMFYUI_PID_FILE"
	printf '%s\n' "ComfyUI did not become ready within 60 seconds. See $COMFYUI_LOG_FILE." >&2
	exit 1
}

is_frontend_ready() {
	curl --silent --fail --max-time 2 "$FRONTEND_URL" >/dev/null 2>&1
}

start_frontend() {
	if is_frontend_ready; then
		printf '%s\n' "Frontend is already running at $FRONTEND_URL."
		return
	fi
	if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
		printf '%s\n' "Frontend package was not found at $FRONTEND_DIR." >&2
		exit 1
	fi
	rm -f "$FRONTEND_PID_FILE"
	printf '%s\n' "Starting frontend at $FRONTEND_URL..."
	(
		cd "$FRONTEND_DIR"
		exec npm run dev -- --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT"
	) >"$FRONTEND_LOG_FILE" 2>&1 &
	local frontend_pid=$!
	printf '%s\n' "$frontend_pid" >"$FRONTEND_PID_FILE"
	for _ in {1..30}; do
		if is_frontend_ready; then
			printf '%s\n' "Frontend is ready."
			return
		fi
		if ! kill -0 "$frontend_pid" 2>/dev/null; then
			rm -f "$FRONTEND_PID_FILE"
			printf '%s\n' "Frontend stopped during startup. See $FRONTEND_LOG_FILE." >&2
			exit 1
		fi
		sleep 1
	done
	kill -TERM "$frontend_pid" 2>/dev/null || true
	rm -f "$FRONTEND_PID_FILE"
	printf '%s\n' "Frontend did not become ready within 30 seconds. See $FRONTEND_LOG_FILE." >&2
	exit 1
}

cd "$PROJECT_ROOT"
start_comfyui
start_frontend

exec uv run uvicorn backend.api.main:app --reload --host 127.0.0.1 --port 8000
