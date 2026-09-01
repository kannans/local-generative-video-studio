# Local Generative Video Studio

The local generative video studio runs on Apple Silicon. It provides:

- PyTorch with Apple Metal Performance Shaders (MPS) detection
- Pydantic-based application settings
- Automatic local storage directory creation
- A health endpoint with runtime, accelerator, disk, and video configuration
- A WebSocket endpoint for generation task progress events
- Phase 3 ComfyUI workflows for text-to-video, continuation, spatial inpainting,
  and VideoToolbox-accelerated composition

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

## ComfyUI And Phase 3 Setup

Phase 3 requires a separate ComfyUI runtime, video model assets, and the
VideoHelperSuite custom node. The local-video-platform virtual environment is
not the ComfyUI environment; install ComfyUI dependencies only into ComfyUI's
own `.venv`.

### 1. Create The ComfyUI Environment

Clone ComfyUI beside this project, create a Python 3.11 environment, and install
its base requirements. Python 3.9 cannot run the current ComfyUI release.

```sh
cd ~/projects/AIML
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI

uv venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt

if [ -f manager_requirements.txt ]; then
	uv pip install --python .venv/bin/python -r manager_requirements.txt
fi
```

If the checkout already exists, omit `git clone` and run the remaining commands
from the ComfyUI directory. Confirm the environment can start ComfyUI:

```sh
cd ~/projects/AIML/ComfyUI
source .venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188
```

Keep this terminal open while ComfyUI is in use. A normal startup reports an MPS
device and ends with the local GUI URL. `comfy-aimdo` warnings about macOS are
informational when ComfyUI selects its eager backend.

### 2. Install VideoHelperSuite

Stop ComfyUI with `Ctrl+C`, then install VideoHelperSuite. It provides
`VHS_LoadVideo` for continuation/inpainting inputs and `VHS_VideoCombine` for
MP4 workflow outputs.

```sh
cd ~/projects/AIML/ComfyUI/custom_nodes

if [ ! -d ComfyUI-VideoHelperSuite ]; then
	git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git
fi

cd ComfyUI-VideoHelperSuite

if [ -f requirements.txt ]; then
	uv pip install \
		--python ~/projects/AIML/ComfyUI/.venv/bin/python \
		-r requirements.txt
fi
```

Some `uv` environments do not expose a standalone `pip` command. Use `uv pip`
as above, or `python -m pip` after installing pip into the ComfyUI environment;
do not run custom-node installs in `local-video-platform/.venv`.

### 3. Download Video Model Assets

Start with a complete **Wan 2.1 Text-to-Video 1.3B** asset bundle on Apple
Silicon. It is a more practical initial model than a 14B variant on a 48 GB
unified-memory system. The pipeline exposes an LTX-Video request option, but
Wan 2.1 is the initial integration target; install one model family for the
first verification run.

In the ComfyUI web UI at `http://127.0.0.1:8188`, use Manager's model/workflow
browser to select a Wan 2.1 T2V workflow and download every listed dependency.
The bundle must include these model classes:

- A Wan 2.1 T2V diffusion model in `models/diffusion_models/`
- A UMT5 text encoder in `models/text_encoders/`
- A Wan 2.1 VAE in `models/vae/`

The Phase 3 request defaults expect filenames similar to
`wan2.1_t2v_1.3B_fp16.safetensors`,
`umt5_xxl_fp8_e4m3fn_scaled.safetensors`, and `wan_2.1_vae.safetensors`.
The ComfyUI registry is authoritative: use its exact detected filenames in a
`VideoGenerationRequest` when your downloaded filenames differ.

### 4. Restart And Verify ComfyUI

Restart ComfyUI after adding custom nodes or model files:

```sh
cd ~/projects/AIML/ComfyUI
source .venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188
```

From another terminal, query the registered nodes and model choices:

```sh
cd ~/projects/AIML/local-video-platform
curl --fail --silent http://127.0.0.1:8188/object_info > /tmp/comfy-object-info.json

uv run python - <<'PY'
import json

with open("/tmp/comfy-object-info.json") as file:
	nodes = json.load(file)

for node_name, field in (
	("UNETLoader", "unet_name"),
	("CLIPLoader", "clip_name"),
	("VAELoader", "vae_name"),
	("VHS_VideoCombine", "format"),
	("VHS_LoadVideo", "video"),
):
	node = nodes.get(node_name)
	value = "MISSING" if node is None else node["input"]["required"][field][0]
	print(f"{node_name}.{field}: {value}")
PY
```

The three loader entries must list the installed Wan/LTX assets, and both VHS
entries must be present before starting an end-to-end Phase 3 inference test.
The server's `object_info` schema is authoritative: verify its node input names
and model filenames before submitting a production render.

### 5. Run Phase 3 Checks

With ComfyUI running and the registry verification passing, validate the Python
modules and submit a text-to-video request:

```sh
cd ~/projects/AIML/local-video-platform
uv run python -m compileall -q backend
uv run python -m backend.pipeline.t2v
```

`backend.pipeline.t2v` submits a 121-frame, 24 fps Wan request. Generated MP4
files are stored in `backend/storage/exports/<run-id>/`; raw latents are copied
to `backend/storage/latents/<run-id>/`. The pipeline modules and VideoToolbox
composition have been smoke-tested locally; a successful render through the
installed Wan/LTX model remains the required final end-to-end verification.

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
| `COMFYUI_PYTHON` | `$COMFYUI_DIR/.venv/bin/python` | Python interpreter from the ComfyUI environment |
| `COMFYUI_HOST` | `127.0.0.1` | Host used for the managed ComfyUI process |
| `COMFYUI_PORT` | `8188` | Port used for the managed ComfyUI process |
| `FRONTEND_DIR` | `frontend` | Next.js application directory |
| `FRONTEND_HOST` | `127.0.0.1` | Host used for the managed frontend process |
| `FRONTEND_PORT` | `3000` | Port used for the managed frontend process |

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
`/system_stats` endpoint is ready, starts the Next.js frontend, and then starts
the FastAPI server. Configure a non-default ComfyUI checkout or interpreter for
that invocation:

```sh
COMFYUI_DIR="$HOME/src/ComfyUI" COMFYUI_PYTHON="$HOME/src/ComfyUI/.venv/bin/python" ./scripts/start_server.sh
```

When ComfyUI is already running on `COMFYUI_HOST:COMFYUI_PORT`, the script uses
that existing process without taking ownership of it. The FastAPI command is:

```sh
uv run uvicorn backend.api.main:app --reload --host 127.0.0.1 --port 8000
```

The web studio is available at <http://127.0.0.1:3000>; the FastAPI service is
available at <http://127.0.0.1:8000>.

Stop the server from another terminal with:

```sh
./scripts/stop_server.sh
```

Use `stop_server.sh` to stop all managed processes. When running the server in
the foreground, `Ctrl+C` stops the FastAPI process; run `stop_server.sh` to also
stop managed ComfyUI and frontend processes.

## Phase 4 Web Studio

The frontend is a Next.js App Router application in `frontend/`. It provides a
responsive dark workspace with project history, backend connection state, a
multiline generation prompt, reference-image selection, 16:9 and 9:16 output
selection, generation progress, and completed-video cards.

Completed-video cards include custom playback controls, looping, a scrubber,
timestamp display, MP4 download, and a paused-frame brush overlay. Brush
strokes are stored as normalized canvas-mask data in the browser and are ready
to be submitted to the Phase 3 inpainting pipeline when that API endpoint is
connected.

Install the frontend dependencies after cloning the repository:

```sh
cd frontend
npm install
```

For frontend-only development, start Next.js directly:

```sh
cd frontend
npm run dev
```

Use `./scripts/start_server.sh` for the normal integrated development workflow;
it manages ComfyUI, Next.js, and FastAPI together.

### WebSocket Contract

The frontend connects to `ws://localhost:8000/ws/generation` and sends a
generation request like this:

```json
{
	"prompt": "A cinematic mountain landscape at sunrise",
	"aspect_ratio": "16:9",
	"reference_name": "optional-reference.png"
}
```

It renders progress from `progress`, `status`, `message`, and `node` fields.
It also accepts future preview events with `type: "preview"` and `preview_url`,
and completion events with `type: "complete"` or `type: "completion"` plus a
`video_url` or `mp4_url` field.

The current FastAPI WebSocket is still a Phase 1 readiness skeleton: it returns
`queued` and `not_implemented` progress messages but does not yet dispatch a
Phase 3 render or send an MP4 URL. The frontend remains usable for prompt and
connection-state interaction; wire the generation worker to the WebSocket to
enable live video cards.

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
	start_server.sh         Start managed ComfyUI, frontend, and FastAPI services
	stop_server.sh          Stop managed ComfyUI, frontend, and FastAPI services
frontend/
	src/app/                Next.js App Router studio shell and styles
	src/components/player/  Video controls and canvas brush overlay
	src/hooks/              WebSocket transport hook
	src/lib/store.ts        Zustand studio, playback, and mask state
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
