# Local Generative Video Studio

Phase 1 is the local FastAPI foundation for a generative video studio running
on Apple Silicon. It provides:

- PyTorch with Apple Metal Performance Shaders (MPS) detection
- Pydantic-based application settings
- Automatic local storage directory creation
- A health endpoint with runtime, accelerator, disk, and video configuration
- A WebSocket endpoint for generation task progress events

## Requirements

- macOS on Apple Silicon, such as an M4 Pro with 48 GB unified memory
- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Ollama running locally if the application needs language-model requests
- A local [ComfyUI](https://github.com/comfyanonymous/ComfyUI) checkout for image or video workflow execution

Verify the package manager before setup:

```sh
uv --version
```

## Installation

From the repository root, initialize the project and install the dependencies:

```sh
uv init --name local-video-platform --python 3.12
uv add fastapi 'uvicorn[standard]' websockets torch torchvision torchaudio aiohttp aiosqlite ffmpeg-python pydantic pydantic-settings
```

For a repeatable setup, use the project script instead:

```sh
./scripts/setup_env.sh
```

The script creates the uv project when `pyproject.toml` is absent, installs the
dependencies, and exits unsuccessfully if
`torch.backends.mps.is_available()` is false.

## Configuration

Settings are defined in [backend/config.py](backend/config.py) and can be
overridden with environment variables or a `.env` file in the repository root.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROJECT_ROOT` | Repository root | Base path for the project |
| `MODEL_CHECKPOINTS_DIR` | `backend/storage/model_checkpoints` | Model checkpoint files |
| `TEMP_FRAMES_DIR` | `backend/storage/temp_frames` | Temporary generated frames |
| `LATENTS_DIR` | `backend/storage/latents` | Session latent files |
| `EXPORTS_DIR` | `backend/storage/exports` | Rendered video exports |
| `VIDEO_WIDTH` | `746` | Video width in pixels |
| `VIDEO_HEIGHT` | `420` | Video height in pixels |
| `FRAME_RATE` | `24` | Frames per second |
| `OLLAMA_API_URL` | `http://localhost:11434` | Ollama service URL |
| `COMFYUI_DIR` | `/Users/kannan.s/projects/AIML/ComfyUI` | ComfyUI checkout containing `main.py` |
| `COMFYUI_PYTHON` | `python3` | Python interpreter from the ComfyUI environment |
| `COMFYUI_HOST` | `127.0.0.1` | Host used for the managed ComfyUI process |
| `COMFYUI_PORT` | `8188` | Port used for the managed ComfyUI process |

The default video format is `746x420` at 24 fps. Portrait video can be selected
with `VIDEO_WIDTH=420` and `VIDEO_HEIGHT=746`.

The API creates all four storage directories during application startup. No
model checkpoints or generated media are included in Phase 1.

## Run The API

Start the development server with hot reload:

```sh
./scripts/start_server.sh
```

`start_server.sh` starts ComfyUI when it is not already running, waits until its
`/system_stats` endpoint is ready, and then starts the FastAPI server. Configure
a non-default ComfyUI checkout or interpreter for that invocation:

```sh
COMFYUI_DIR="$HOME/src/ComfyUI" COMFYUI_PYTHON="$HOME/src/ComfyUI/.venv/bin/python" ./scripts/start_server.sh
```

When ComfyUI is already running on `COMFYUI_HOST:COMFYUI_PORT`, the script uses
that existing process without taking ownership of it. The FastAPI command is:

```sh
uv run uvicorn backend.api.main:app --reload --host 127.0.0.1 --port 8000
```

The service is available at <http://127.0.0.1:8000>.

Stop the server from another terminal with:

```sh
./scripts/stop_server.sh
```

When running the server in the foreground, `Ctrl+C` also performs a clean
shutdown.

## HTTP API

### `GET /health`

Check service and runtime state:

```sh
curl http://127.0.0.1:8000/health
```

The JSON response includes the service status, timestamp, Python and PyTorch
versions, MPS availability, disk capacity, storage paths, video settings, and
the configured Ollama URL.

### `WS /ws/generation`

Connect to:

```text
ws://127.0.0.1:8000/ws/generation
```

Send a JSON generation request. The current Phase 1 skeleton responds with a
queued progress event followed by a worker-readiness event. For example:

```json
{"prompt":"A cinematic mountain landscape at sunrise"}
```

The endpoint keeps the connection open for subsequent requests. The generation
worker and media pipeline will be connected in a later phase.

## Project Layout

```text
backend/
	api/main.py             FastAPI app, health route, and WebSocket route
	config.py               Pydantic Settings and storage paths
	storage/
		model_checkpoints/    Model checkpoint files
		temp_frames/          Temporary frame scratchpad
		latents/              Session latent files
		exports/              Final exports
scripts/
	setup_env.sh            Initialize, install, and verify MPS
	start_server.sh         Start the local uvicorn development server
	stop_server.sh          Stop the local uvicorn process
pyproject.toml            Project metadata and dependencies
uv.lock                   Reproducible dependency lockfile
```

## Troubleshooting

If setup reports that MPS is unavailable, confirm that the terminal is using a
native Apple Silicon Python environment and rerun:

```sh
uv run python -c 'import torch; print(torch.backends.mps.is_available())'
```

If port 8000 is already occupied, use another port:

```sh
uv run uvicorn backend.api.main:app --reload --host 127.0.0.1 --port 8001
```
