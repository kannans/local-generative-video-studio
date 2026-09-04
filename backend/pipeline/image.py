"""Prompt-driven still image and animated GIF generation workflows."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.inference.comfy_client import ComfyProgressEvent, ComfyUIClient, ComfyUIClientError
from backend.pipeline.t2v import GeneratedVideo, TextToVideoEngine, VideoGenerationRequest


ImageStyle = Literal["photo", "3d", "graphic", "art", "gif"]

STYLE_PROMPTS: dict[ImageStyle, str] = {
    "photo": "Professional photograph, natural light, realistic detail. ",
    "3d": "Polished 3D render, physically based materials, cinematic lighting. ",
    "graphic": "Refined graphic design, strong composition, crisp shapes, intentional typography. ",
    "art": "Expressive fine art image, rich texture, considered color and composition. ",
    "gif": "Seamless short looping animation. ",
}


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    """Inputs for a FLUX still or LTX animated GIF generation."""

    prompt: str
    style: ImageStyle = "photo"
    checkpoint: str = "flux1-schnell-fp8.safetensors"
    width: int = 768
    height: int = 768
    seed: int = 0
    steps: int = 4

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if self.width < 64 or self.height < 64 or self.width % 16 or self.height % 16:
            raise ValueError("image dimensions must be at least 64 pixels and divisible by 16")
        if self.steps <= 0:
            raise ValueError("steps must be positive")

    @property
    def styled_prompt(self) -> str:
        return f"{STYLE_PROMPTS[self.style]}{self.prompt.strip()}"


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    """A locally persisted generated image."""

    run_id: str
    prompt_id: str
    image_path: Path
    files: tuple[Path, ...]
    request: ImageGenerationRequest


class ImageGenerationEngine:
    """Generate FLUX still images and LTX animated GIFs through ComfyUI."""

    def __init__(self, client: ComfyUIClient, text_to_video: TextToVideoEngine) -> None:
        self.client = client
        self.text_to_video = text_to_video

    @staticmethod
    def build_workflow(request: ImageGenerationRequest, output_prefix: str) -> dict[str, Any]:
        """Build a four-step FLUX.1 Schnell checkpoint workflow."""
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": request.checkpoint}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": request.styled_prompt, "clip": ["1", 1]}},
            "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
            "4": {"class_type": "EmptySD3LatentImage", "inputs": {"width": request.width, "height": request.height, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"seed": request.seed, "steps": request.steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": f"{output_prefix}/image"}},
        }

    async def generate(
        self,
        request: ImageGenerationRequest,
        run_id: str,
        progress_callback: Callable[[ComfyProgressEvent], Awaitable[None]] | None = None,
    ) -> GeneratedImage:
        if request.style == "gif":
            return await self._generate_gif(request, run_id, progress_callback)
        prompt_id, client_id = await self.client.submit_workflow(self.build_workflow(request, run_id))
        async for event in self.client.monitor_workflow(prompt_id, client_id):
            if progress_callback is not None:
                await progress_callback(event)
        files = tuple(await self.client.save_outputs(prompt_id, self.text_to_video.settings.exports_dir))
        image_path = self._required_image(files)
        return GeneratedImage(run_id, prompt_id, image_path, files, request)

    async def _generate_gif(
        self,
        request: ImageGenerationRequest,
        run_id: str,
        progress_callback: Callable[[ComfyProgressEvent], Awaitable[None]] | None,
    ) -> GeneratedImage:
        video_request = VideoGenerationRequest(
            prompt=request.styled_prompt,
            model="ltx-video",
            checkpoint="ltxv-13b-0.9.8-distilled-fp8.safetensors",
            text_encoder="t5xxl_fp8_e4m3fn_scaled.safetensors",
            seed=request.seed,
            frames=49,
            width=request.width,
            height=request.height,
            fps=12,
            steps=8,
            cfg=1.0,
        )
        generated: GeneratedVideo = await self.text_to_video.run_and_wait(video_request, run_id, progress_callback)
        gif_path = generated.video_path.with_name("image.gif")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(generated.video_path),
            "-vf", "fps=12,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            "-loop", "0", str(gif_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise ComfyUIClientError(f"Could not encode animated GIF: {stderr.decode(errors='replace')}")
        return GeneratedImage(run_id, generated.prompt_id, gif_path, (*generated.files, gif_path), request)

    @staticmethod
    def _required_image(files: tuple[Path, ...]) -> Path:
        image = next((path for path in files if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}), None)
        if image is None:
            raise ComfyUIClientError("ComfyUI did not return an image artifact")
        return image
