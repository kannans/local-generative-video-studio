#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

command -v uv >/dev/null 2>&1 || {
  printf '%s\n' "uv is required. Install it from https://docs.astral.sh/uv/" >&2
  exit 1
}

if [[ ! -f pyproject.toml ]]; then
  uv init --name local-video-platform --python 3.12
fi

uv add fastapi 'uvicorn[standard]' websockets torch torchvision torchaudio \
  aiohttp ffmpeg-python pydantic pydantic-settings

uv run python - <<'PY'
import platform
import torch

print(f"Python: {platform.python_version()}")
print(f"Torch: {torch.__version__}")
print(f"Apple Silicon: {platform.machine() == 'arm64'}")
print(f"MPS available: {torch.backends.mps.is_available()}")
if not torch.backends.mps.is_available():
    raise SystemExit("MPS is unavailable; check the native Apple Silicon Python environment.")
PY

printf '%s\n' "Environment ready. Start the API with: uv run uvicorn backend.api.main:app --reload"
