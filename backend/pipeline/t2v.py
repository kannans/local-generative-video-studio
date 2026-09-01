"""Text-to-video workflow construction and execution for ComfyUI video models."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import uuid4

from backend.config import Settings, settings
from backend.inference.comfy_client import ComfyUIClient, ComfyUIClientError

VideoModel = Literal["wan-2.1", "ltx-video"]


@dataclass(frozen=True, slots=True)
class VideoGenerationRequest:
    """Inputs shared by text-to-video and image-conditioned video generation."""

    prompt: str
    negative_prompt: str = ""
    model: VideoModel = "wan-2.1"
    checkpoint: str = "wan2.1_t2v_1.3B_fp16.safetensors"
    vae: str = "wan_2.1_vae.safetensors"
    text_encoder: str = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
    seed: int = 0
    frames: int = 121
    width: int = 746
    height: int = 420
    fps: int = 24
    steps: int = 30
    cfg: float = 6.0
    sampler_name: str = "euler"
    scheduler: str = "simple"
    denoise: float = 1.0

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if not 1 <= self.frames <= 144:
            raise ValueError("frames must be between 1 and 144")
        if self.width < 16 or self.height < 16:
            raise ValueError("video dimensions must be at least 16 pixels")
        if self.width % 2 or self.height % 2:
            raise ValueError("video dimensions must be even for H.264 output")
        if self.fps <= 0 or self.steps <= 0 or not 0 < self.denoise <= 1:
            raise ValueError("fps and steps must be positive and denoise must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class GeneratedVideo:
    """Locally persisted output artifacts for one generated video segment."""

    run_id: str
    prompt_id: str
    video_path: Path
    latent_path: Path
    files: tuple[Path, ...]
    request: VideoGenerationRequest


class TextToVideoEngine:
    """Submit Wan 2.1 or LTX-Video generation workflows to ComfyUI."""

    def __init__(self, client: ComfyUIClient, app_settings: Settings = settings) -> None:
        self.client = client
        self.settings = app_settings

    def build_workflow(self, request: VideoGenerationRequest, output_prefix: str) -> dict[str, Any]:
        """Build a ComfyUI API workflow using the bundled native video nodes.

        Wan and LTX use the same ComfyUI model-loader/sample/decode contract;
        `model` selects the loader's architecture profile. The `VHS_VideoCombine`
        node is supplied by VideoHelperSuite and produces a real MP4 artifact.
        """
        workflow: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": request.checkpoint, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": request.text_encoder, "type": "wan" if request.model == "wan-2.1" else "ltxv"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": request.vae}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": request.prompt, "clip": ["2", 0]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": request.negative_prompt, "clip": ["2", 0]}},
            "6": {"class_type": "EmptyLatentImage", "inputs": {"width": request.width, "height": request.height, "batch_size": request.frames}},
            "7": {"class_type": "KSampler", "inputs": {"seed": request.seed, "steps": request.steps, "cfg": request.cfg, "sampler_name": request.sampler_name, "scheduler": request.scheduler, "denoise": request.denoise, "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
            "9": {"class_type": "SaveLatent", "inputs": {"samples": ["7", 0], "filename_prefix": f"{output_prefix}/latent"}},
            "10": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["8", 0], "frame_rate": request.fps, "loop_count": 0, "filename_prefix": f"{output_prefix}/video", "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
        }
        return workflow

    async def generate(self, request: VideoGenerationRequest, run_id: str | None = None, progress_callback: Callable[[Any], Awaitable[None]] | None = None) -> GeneratedVideo:
        """Run a 4-6 second text-to-video generation and persist its artifacts."""
        resolved_run_id = run_id or uuid4().hex
        workflow = self.build_workflow(request, resolved_run_id)
        prompt_id, client_id = await self.client.submit_workflow(workflow)
        async for event in self.client.monitor_workflow(prompt_id, client_id):
            if progress_callback is not None:
                await progress_callback(event)
        files = tuple(await self.client.save_outputs(prompt_id, self.settings.exports_dir))
        video_path = self._required_artifact(files, {".mp4", ".mov", ".mkv"}, "video")
        latent_path = self.store_latent(
            self._required_artifact(files, {".latent", ".safetensors"}, "latent"), resolved_run_id
        )
        return GeneratedVideo(resolved_run_id, prompt_id, video_path, latent_path, files, request)

    async def run_and_wait(self, request: VideoGenerationRequest, run_id: str | None = None, progress_callback: Callable[[Any], Awaitable[None]] | None = None) -> GeneratedVideo:
        """Run generation after verifying that the configured VideoHelperSuite node exists."""
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is required to encode ComfyUI video output")
        return await self.generate(request, run_id, progress_callback)

    def _run_directory(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError("run_id must be a simple directory name")
        directory = self.settings.exports_dir / run_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def store_latent(self, source: Path, run_id: str) -> Path:
        """Copy a raw sampler latent into the studio's dedicated latent store."""
        destination = self.settings.latents_dir / run_id / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    @staticmethod
    def _required_artifact(files: tuple[Path, ...], extensions: set[str], label: str) -> Path:
        match = next((path for path in files if path.suffix.lower() in extensions), None)
        if match is None:
            raise ComfyUIClientError(f"ComfyUI did not return a {label} artifact")
        return match


def build_text_to_video_workflow(request: VideoGenerationRequest, output_prefix: str) -> dict[str, Any]:
    """Convenience function for validating/exporting a T2V workflow without a server."""
    return TextToVideoEngine(ComfyUIClient()).build_workflow(request, output_prefix)


async def _integration_main() -> None:
    request = VideoGenerationRequest(prompt="A cinematic tide rolling across black sand", frames=121)
    generated = await TextToVideoEngine(ComfyUIClient()).run_and_wait(request)
    print(generated.video_path)


if __name__ == "__main__":
    asyncio.run(_integration_main())
