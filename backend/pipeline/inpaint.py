"""Targeted spatial video inpainting workflows driven by masks or brush coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from backend.inference.comfy_client import ComfyProgressEvent, ComfyUIClientError
from backend.pipeline.t2v import GeneratedVideo, TextToVideoEngine, VideoGenerationRequest


@dataclass(frozen=True, slots=True)
class MaskRegion:
    """A normalized rectangular user-brush region in the source video."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(0 <= value <= 1 for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("mask coordinates must be normalized values in [0, 1]")
        if self.width == 0 or self.height == 0 or self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("mask region must be non-empty and lie within the frame")


class SpatialInpaintEngine:
    """Construct temporal inpainting jobs without regenerating unaffected source areas."""

    def __init__(self, text_to_video: TextToVideoEngine) -> None:
        self.text_to_video = text_to_video

    def build_workflow(self, source_video_name: str, mask_name: str, request: VideoGenerationRequest, output_prefix: str) -> dict[str, Any]:
        """Build a masked latent-denoising workflow for a SAM2 or painted mask sequence."""
        if not source_video_name or not mask_name:
            raise ValueError("source_video_name and mask_name are required")
        workflow = self.text_to_video.build_workflow(request, output_prefix)
        workflow.update({
            "11": {"class_type": "VHS_LoadVideo", "inputs": {"video": source_video_name, "force_rate": request.fps, "force_size": "Disabled", "frame_load_cap": request.frames, "skip_first_frames": 0, "select_every_nth": 1}},
            "12": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
            "13": {"class_type": "ImageToMask", "inputs": {"image": ["12", 0], "channel": "red"}},
            "6": {"class_type": "VAEEncodeForInpaint", "inputs": {"pixels": ["11", 0], "vae": ["3", 0], "mask": ["13", 0], "grow_mask_by": 12}},
        })
        return workflow

    async def inpaint(self, source_video: Path, mask: Path, request: VideoGenerationRequest, run_id: str, progress_callback: Callable[[ComfyProgressEvent], Awaitable[None]] | None = None) -> GeneratedVideo:
        """Upload source and mask, then render only the masked latent region."""
        if not source_video.is_file() or not mask.is_file():
            raise FileNotFoundError("source_video and mask must both exist")
        client = self.text_to_video.client
        source_name, mask_name = await client.upload_input(source_video), await client.upload_input(mask)
        workflow = self.build_workflow(source_name, mask_name, request, run_id)
        prompt_id, client_id = await client.submit_workflow(workflow)
        async for event in client.monitor_workflow(prompt_id, client_id):
            if progress_callback is not None:
                await progress_callback(event)
        files = tuple(await client.save_outputs(prompt_id, self.text_to_video.settings.exports_dir))
        video = self.text_to_video._required_artifact(files, {".mp4", ".mov", ".mkv"}, "video")
        latent = self.text_to_video.store_latent(
            self.text_to_video._required_artifact(files, {".latent", ".safetensors"}, "latent"), run_id
        )
        return GeneratedVideo(run_id, prompt_id, video, latent, files, request)

    @staticmethod
    def brush_mask_filter(region: MaskRegion, width: int, height: int) -> str:
        """Return an FFmpeg filter that rasterizes a normalized rectangular brush mask."""
        x, y = round(region.x * width), round(region.y * height)
        mask_width, mask_height = round(region.width * width), round(region.height * height)
        return f"drawbox=x={x}:y={y}:w={mask_width}:h={mask_height}:color=white:t=fill"
