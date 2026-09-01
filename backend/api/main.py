"""FastAPI application for the local video studio."""

import platform
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncIterator

import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings


def mps_is_available() -> bool:
    """Check whether PyTorch can use Apple's Metal Performance Shaders backend."""
    return bool(torch.backends.mps.is_available())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create required local storage before accepting traffic."""
    for directory in settings.storage_directories:
        directory.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Local Generative Video Studio",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, object]:
    """Return service, runtime, accelerator, and storage information."""
    disk = shutil.disk_usage(settings.project_root)
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "mps_available": mps_is_available(),
        },
        "storage": {
            "model_checkpoints": str(settings.model_checkpoints_dir),
            "temp_frames": str(settings.temp_frames_dir),
            "latents": str(settings.latents_dir),
            "exports": str(settings.exports_dir),
            "disk_free_bytes": disk.free,
            "disk_total_bytes": disk.total,
        },
        "video": {
            "resolution": settings.resolution,
            "width": settings.video_width,
            "height": settings.video_height,
            "frame_rate": settings.frame_rate,
        },
        "ollama_api_url": settings.ollama_api_url,
    }


@app.websocket("/ws/generation")
async def generation_websocket(websocket: WebSocket) -> None:
    """Accept generation requests and stream progress events."""
    await websocket.accept()
    try:
        while True:
            request = await websocket.receive_json()
            await websocket.send_json(
                {
                    "type": "progress",
                    "status": "queued",
                    "progress": 0,
                    "request": request,
                }
            )
            await websocket.send_json(
                {
                    "type": "progress",
                    "status": "not_implemented",
                    "progress": 0,
                    "message": "Generation worker is ready to be connected.",
                }
            )
    except WebSocketDisconnect:
        return
