"""Segment continuation using the final frame of the previous generated video."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from backend.pipeline.t2v import GeneratedVideo, TextToVideoEngine, VideoGenerationRequest


class VideoContinuationEngine:
    """Generate temporally conditioned five-second continuations from a segment."""

    def __init__(self, text_to_video: TextToVideoEngine) -> None:
        self.text_to_video = text_to_video

    def extract_final_frame(self, video_path: Path, destination: Path | None = None) -> Path:
        """Extract the final decoded frame with FFmpeg for I2V conditioning."""
        if not video_path.is_file():
            raise FileNotFoundError(video_path)
        frame_path = destination or self.text_to_video.settings.temp_frames_dir / f"{video_path.stem}-last.png"
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(video_path), "-frames:v", "1", str(frame_path)], check=True, capture_output=True, text=True)
        return frame_path

    def build_workflow(self, request: VideoGenerationRequest, input_frame_name: str, output_prefix: str) -> dict[str, object]:
        """Build I2V workflow, injecting the preceding segment's terminal frame."""
        if not input_frame_name or Path(input_frame_name).is_absolute() or ".." in Path(input_frame_name).parts:
            raise ValueError("input_frame_name must be a relative ComfyUI input path")
        workflow = self.text_to_video.build_workflow(request, output_prefix)
        workflow["6"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["11", 0], "vae": ["3", 0]}}
        workflow["11"] = {"class_type": "LoadImage", "inputs": {"image": input_frame_name}}
        workflow["7"]["inputs"]["latent_image"] = ["6", 0]
        return workflow

    async def continue_video(self, previous: GeneratedVideo, request: VideoGenerationRequest, run_id: str | None = None, progress_callback: Callable[[object], Awaitable[None]] | None = None) -> GeneratedVideo:
        """Create a five-second continuation retaining the previous segment's visual state."""
        continuation = replace(request, frames=request.fps * 5)
        frame = self.extract_final_frame(previous.video_path)
        resolved_run_id = run_id or f"{previous.run_id}-next-{uuid4().hex[:8]}"
        client = self.text_to_video.client
        workflow = self.build_workflow(continuation, await client.upload_input(frame), resolved_run_id)
        prompt_id, client_id = await client.submit_workflow(workflow)
        async for event in client.monitor_workflow(prompt_id, client_id):
            if progress_callback is not None:
                await progress_callback(event)
        files = tuple(await client.save_outputs(prompt_id, self.text_to_video.settings.exports_dir))
        latent = self.text_to_video.store_latent(
            self.text_to_video._required_artifact(files, {".latent", ".safetensors"}, "latent"), resolved_run_id
        )
        return GeneratedVideo(resolved_run_id, prompt_id, self.text_to_video._required_artifact(files, {".mp4", ".mov", ".mkv"}, "video"), latent, files, continuation)


if __name__ == "__main__":
    raise SystemExit("Use VideoContinuationEngine from an async application.")
