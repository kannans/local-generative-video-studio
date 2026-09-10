"""Generation API routes and real-time ComfyUI job dispatching."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from backend.config import settings
from backend.inference.comfy_client import ComfyProgressEvent, ComfyUIClient, ComfyUIClientError
from backend.pipeline.image import GeneratedImage, ImageGenerationEngine, ImageGenerationRequest, ImageStyle
from backend.pipeline.i2v import VideoContinuationEngine
from backend.pipeline.inpaint import SpatialInpaintEngine
from backend.pipeline.t2v import GeneratedVideo, TextToVideoEngine, VideoGenerationRequest

router = APIRouter()
logger = logging.getLogger(__name__)


class GenerationPayload(BaseModel):
    """Public controls for a text-to-video generation job."""

    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    quality: Literal["draft", "final"] = "draft"
    model: Literal["wan-2.1", "ltx-video"] = "wan-2.1"
    seed: int = Field(default=settings.generation_seed, ge=0)
    width: int | None = Field(default=None, ge=16)
    height: int | None = Field(default=None, ge=16)
    frames: int | None = Field(default=None, ge=1, le=144)
    fps: int | None = Field(default=None, gt=0)
    steps: int | None = Field(default=None, gt=0)
    cfg: float | None = Field(default=None, gt=0)

    def to_request(self) -> VideoGenerationRequest:
        if self.quality == "draft":
            width, height = settings.generation_width, settings.generation_height
            frames = settings.generation_frames
            fps, steps, cfg = settings.generation_fps, settings.generation_steps, settings.generation_cfg
        else:
            width, height = settings.video_width, settings.video_height
            frames, fps, steps, cfg = 121, settings.frame_rate, 20, 5.5
        width = self.width if self.width is not None else width
        height = self.height if self.height is not None else height
        if self.aspect_ratio == "9:16":
            width, height = height, width
        checkpoint = (
            "wan2.1_t2v_1.3B_fp16.safetensors"
            if self.model == "wan-2.1"
            else "ltxv-13b-0.9.8-distilled-fp8.safetensors"
        )
        text_encoder = (
            "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
            if self.model == "wan-2.1"
            else "t5xxl_fp8_e4m3fn_scaled.safetensors"
        )
        return VideoGenerationRequest(
            prompt=self.prompt,
            negative_prompt=self.negative_prompt,
            model=self.model,
            checkpoint=checkpoint,
            text_encoder=text_encoder,
            seed=self.seed,
            frames=self.frames if self.frames is not None else frames,
            width=width,
            height=height,
            fps=self.fps if self.fps is not None else fps,
            steps=self.steps if self.steps is not None else steps,
            cfg=self.cfg if self.cfg is not None else cfg,
        )


class ImageGenerationPayload(BaseModel):
    """Public controls for a prompt-driven image or animated GIF job."""

    generation_type: Literal["image"] = "image"
    prompt: str = Field(min_length=1)
    style: ImageStyle = "photo"
    aspect_ratio: Literal["1:1", "16:9", "9:16"] = "1:1"
    width: int | None = Field(default=None, ge=64)
    height: int | None = Field(default=None, ge=64)
    steps: int = Field(default=4, ge=1, le=12)
    seed: int = Field(default=settings.generation_seed, ge=0)

    def to_request(self) -> ImageGenerationRequest:
        dimensions = {
            "1:1": (768, 768),
            "16:9": (1024, 576),
            "9:16": (576, 1024),
        }
        width, height = dimensions[self.aspect_ratio]
        if self.style == "gif":
            width, height = {
                "1:1": (512, 512),
                "16:9": (512, 288),
                "9:16": (288, 512),
            }[self.aspect_ratio]
        elif self.width is not None and self.height is not None:
            width, height = self.width, self.height
        return ImageGenerationRequest(
            prompt=self.prompt,
            style=self.style,
            width=_align_image_dimension(width),
            height=_align_image_dimension(height),
            steps=self.steps,
            seed=self.seed,
        )


class ExtendPayload(GenerationPayload):
    """Controls for continuing a previously exported generation."""

    previous_run_id: str = Field(min_length=1)


class InpaintPayload(GenerationPayload):
    """Controls and local source assets for an inpainting job."""

    source_video: str = Field(min_length=1)
    mask: str = Field(min_length=1)


GeneratedMedia = GeneratedVideo | GeneratedImage
JobFactory = Callable[[Callable[[ComfyProgressEvent], Awaitable[None]], str], Awaitable[GeneratedMedia]]


class GenerationDispatcher:
    """Run ComfyUI jobs off-request and fan their events out to browser clients."""

    def __init__(self) -> None:
        client = ComfyUIClient()
        text_to_video = TextToVideoEngine(client)
        self.text_to_video = text_to_video
        self.image = ImageGenerationEngine(client, text_to_video)
        self.continuation = VideoContinuationEngine(text_to_video)
        self.inpaint_engine = SpatialInpaintEngine(text_to_video)
        self.connections: set[WebSocket] = set()
        self.tasks: set[asyncio.Task[None]] = set()

    def schedule(self, background_tasks: BackgroundTasks, factory: JobFactory) -> str:
        """Queue a job after the HTTP response has been sent."""
        job_id = uuid4().hex
        logger.info("Generation job %s queued from HTTP", job_id)
        background_tasks.add_task(self.run, job_id, factory)
        return job_id

    def start(self, factory: JobFactory) -> str:
        """Queue a job originating from an already-connected WebSocket client."""
        job_id = uuid4().hex
        logger.info("Generation job %s queued from WebSocket", job_id)
        task = asyncio.create_task(self.run(job_id, factory), name=f"generation-{job_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job_id

    async def run(self, job_id: str, factory: JobFactory) -> None:
        reported_progress = 0
        logged_progress = -1

        async def report(event: ComfyProgressEvent) -> None:
            nonlocal logged_progress, reported_progress
            progress = event.progress
            if progress is not None:
                reported_progress = round(progress * 100)
            if event.event_type == "execution_start":
                logger.info("Generation job %s started as ComfyUI prompt %s", job_id, event.prompt_id)
            elif progress is not None and reported_progress != logged_progress:
                logger.info(
                    "Generation job %s progress %d%% at node %s",
                    job_id,
                    reported_progress,
                    event.node_id or "unknown",
                )
                logged_progress = reported_progress
            else:
                logger.info(
                    "Generation job %s event=%s progress=%d%% node=%s",
                    job_id,
                    event.event_type,
                    reported_progress,
                    event.node_id or "unknown",
                )
            await self.broadcast(
                {
                    "type": "progress",
                    "job_id": job_id,
                    "status": "rendering",
                    "progress": reported_progress,
                    "node": event.node_id,
                    "message": event.event_type.replace("_", " ").title(),
                }
            )

        await self.broadcast({"type": "progress", "job_id": job_id, "status": "queued", "progress": 0})
        try:
            generated = await factory(report, job_id)
        except ComfyUIClientError as error:
            if str(error) == "Generation interrupted":
                logger.info("Generation job %s cancelled", job_id)
                await self.broadcast(
                    {
                        "type": "cancelled",
                        "job_id": job_id,
                        "status": "cancelled",
                        "progress": 0,
                        "message": "Generation cancelled",
                    }
                )
                return
            logger.exception("Generation job %s failed: %s", job_id, error)
            await self.broadcast({"type": "error", "job_id": job_id, "status": "failed", "progress": 0, "message": str(error)})
            return
        except Exception as error:
            logger.exception("Generation job %s failed: %s", job_id, error)
            await self.broadcast({"type": "error", "job_id": job_id, "status": "failed", "progress": 0, "message": str(error)})
            return
        media_path = generated.image_path if isinstance(generated, GeneratedImage) else generated.video_path
        media_key = "image_url" if isinstance(generated, GeneratedImage) else "video_url"
        logger.info("Generation job %s completed; export=%s", job_id, media_path)
        await self.broadcast(
            {
                "type": "complete",
                "job_id": job_id,
                "status": "complete",
                "progress": 100,
                media_key: export_url(media_path),
                "run_id": generated.run_id,
            }
        )

    async def broadcast(self, event: dict[str, object]) -> None:
        disconnected: list[WebSocket] = []
        for connection in tuple(self.connections):
            try:
                await connection.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                disconnected.append(connection)
        for connection in disconnected:
            self.connections.discard(connection)

    async def close(self) -> None:
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


dispatcher = GenerationDispatcher()


def _align_image_dimension(value: int) -> int:
    """Snap FLUX latent sizes to the 16-pixel grid required by EmptySD3LatentImage."""
    return max(64, value - (value % 16))


def export_url(video_path: Path) -> str:
    """Return a URL beneath the exports static mount for a generated video."""
    relative_path = video_path.resolve().relative_to(settings.exports_dir.resolve())
    return f"/exports/{quote(relative_path.as_posix())}"


def export_path(path: str) -> Path:
    """Resolve a client-selected export while preventing filesystem traversal."""
    candidate = (settings.exports_dir / path).resolve()
    if settings.exports_dir.resolve() not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Export was not found")
    return candidate


def export_directory(path: str) -> Path:
    """Resolve an exported run directory while preventing filesystem traversal."""
    candidate = (settings.exports_dir / path).resolve()
    if settings.exports_dir.resolve() not in candidate.parents or not candidate.is_dir():
        raise HTTPException(status_code=404, detail="Previous run was not found")
    return candidate


def previous_video(run_id: str) -> GeneratedVideo:
    """Rehydrate the artifacts required by the continuation pipeline."""
    run_directory = export_directory(run_id)
    video = next((path for path in run_directory.rglob("*") if path.suffix.lower() == ".mp4"), None)
    latent_directory = settings.latents_dir / run_id
    latent = next((path for path in latent_directory.rglob("*") if path.is_file()), None)
    if video is None or latent is None:
        raise HTTPException(status_code=404, detail="Previous run is missing required artifacts")
    return GeneratedVideo(run_id, "", video, latent, (video, latent), VideoGenerationRequest(prompt="Continuation source"))


@router.post("/generate", status_code=202)
async def generate(payload: GenerationPayload, background_tasks: BackgroundTasks) -> dict[str, str]:
    async def job(report: Callable[[ComfyProgressEvent], Awaitable[None]], run_id: str) -> GeneratedVideo:
        return await dispatcher.text_to_video.run_and_wait(payload.to_request(), run_id, report)

    return {"job_id": dispatcher.schedule(background_tasks, job), "status": "queued"}


@router.post("/generation/cancel")
async def cancel_generation() -> dict[str, str]:
    """Interrupt the ComfyUI workflow currently consuming local compute."""
    logger.info("Cancelling current ComfyUI generation")
    await dispatcher.text_to_video.client.interrupt()
    await dispatcher.broadcast(
        {"type": "cancelled", "status": "cancelled", "progress": 0, "message": "Current generation cancelled"}
    )
    return {"status": "cancelled"}


@router.post("/generation/clear-queue")
async def clear_generation_queue() -> dict[str, str]:
    """Remove ComfyUI jobs that have not begun execution."""
    logger.info("Clearing pending ComfyUI generation queue")
    await dispatcher.text_to_video.client.clear_queue()
    await dispatcher.broadcast(
        {"type": "queue_cleared", "status": "cleared", "progress": 0, "message": "Pending generation queue cleared"}
    )
    return {"status": "cleared"}


@router.post("/extend", status_code=202)
async def extend(payload: ExtendPayload, background_tasks: BackgroundTasks) -> dict[str, str]:
    previous = previous_video(payload.previous_run_id)

    async def job(report: Callable[[ComfyProgressEvent], Awaitable[None]], run_id: str) -> GeneratedVideo:
        return await dispatcher.continuation.continue_video(previous, payload.to_request(), run_id, report)

    return {"job_id": dispatcher.schedule(background_tasks, job), "status": "queued"}


@router.post("/inpaint", status_code=202)
async def inpaint(payload: InpaintPayload, background_tasks: BackgroundTasks) -> dict[str, str]:
    source_video = export_path(payload.source_video)
    mask = export_path(payload.mask)

    async def job(report: Callable[[ComfyProgressEvent], Awaitable[None]], run_id: str) -> GeneratedVideo:
        return await dispatcher.inpaint_engine.inpaint(source_video, mask, payload.to_request(), run_id, report)

    return {"job_id": dispatcher.schedule(background_tasks, job), "status": "queued"}


@router.websocket("/ws/generation")
async def generation_websocket(websocket: WebSocket) -> None:
    """Accept frontend jobs and relay ComfyUI execution events to all clients."""
    await websocket.accept()
    dispatcher.connections.add(websocket)
    try:
        while True:
            raw_payload = await websocket.receive_json()
            try:
                if raw_payload.get("generation_type") == "image":
                    image_request = ImageGenerationPayload.model_validate(raw_payload).to_request()

                    async def image_job(
                        report: Callable[[ComfyProgressEvent], Awaitable[None]],
                        run_id: str,
                        request: ImageGenerationRequest = image_request,
                    ) -> GeneratedImage:
                        return await dispatcher.image.generate(request, run_id, report)

                    job = image_job
                else:
                    video_request = GenerationPayload.model_validate(raw_payload).to_request()

                    async def video_job(
                        report: Callable[[ComfyProgressEvent], Awaitable[None]],
                        run_id: str,
                        request: VideoGenerationRequest = video_request,
                    ) -> GeneratedVideo:
                        return await dispatcher.text_to_video.run_and_wait(request, run_id, report)

                    job = video_job
            except (ValidationError, ValueError) as error:
                logger.warning("Rejected generation request: %s", error)
                await websocket.send_json(
                    {"type": "error", "status": "failed", "progress": 0, "message": str(error)}
                )
                continue

            await websocket.send_json({"type": "accepted", "job_id": dispatcher.start(job), "status": "queued", "progress": 0})
    except WebSocketDisconnect:
        pass
    finally:
        dispatcher.connections.discard(websocket)
