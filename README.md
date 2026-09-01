Local Generative Video Studio
=============================

Phase 1 provides a FastAPI service, Apple Metal availability verification, and
local storage directories for model checkpoints, temporary frames, latents, and
exports.

Setup
-----

	uv init --name local-video-platform --python 3.12
	uv add fastapi 'uvicorn[standard]' websockets torch torchvision torchaudio aiohttp ffmpeg-python pydantic pydantic-settings

The same setup can be repeated with:

	./scripts/setup_env.sh

The script verifies `torch.backends.mps.is_available()` and exits with an error
if the Metal backend is unavailable.

API
---

Start the development server:

	uv run uvicorn backend.api.main:app --reload --host 127.0.0.1 --port 8000

The health endpoint is available at `http://127.0.0.1:8000/health` and the
generation WebSocket is available at `ws://127.0.0.1:8000/ws/generation`.
