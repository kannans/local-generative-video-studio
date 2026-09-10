"""FastAPI application for the local video studio."""

import platform
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncIterator

import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.routes import dispatcher, router
from backend.config import settings
from backend.pipeline.voice import voice_status


def mps_is_available() -> bool:
    """Check whether PyTorch can use Apple's Metal Performance Shaders backend."""
    return bool(torch.backends.mps.is_available())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create required local storage before accepting traffic."""
    for directory in settings.storage_directories:
        directory.mkdir(parents=True, exist_ok=True)
    yield
    await dispatcher.close()


app = FastAPI(
    title="Local Generative Video Studio",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.mount("/exports", StaticFiles(directory=settings.exports_dir), name="exports")


@app.get("/health")
async def health() -> dict[str, object]:
    """Return service, runtime, accelerator, and storage information."""
    disk = shutil.disk_usage(settings.project_root)
    status = voice_status()
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
            "voices": str(settings.voices_dir),
        },
        "voice": {
            "available": list(status.available),
            "optional": list(status.optional),
            "default": status.default,
            "references": status.references,
            "install_hint": status.install_hint,
        },
        "video": {
            "resolution": settings.resolution,
            "width": settings.video_width,
            "height": settings.video_height,
            "frame_rate": settings.frame_rate,
        },
        "ollama_api_url": settings.ollama_api_url,
    }
