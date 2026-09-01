"""Hardware-accelerated FFmpeg composition for generated video segments."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


class VideoComposer:
    """Compose normalized 420p H.264 videos with Apple's VideoToolbox encoder."""

    def __init__(self, ffmpeg_binary: str = "ffmpeg", width: int = 746, height: int = 420, fps: int = 24) -> None:
        if width % 2 or height % 2 or min(width, height, fps) <= 0:
            raise ValueError("width and height must be positive even values and fps must be positive")
        self.ffmpeg_binary, self.width, self.height, self.fps = ffmpeg_binary, width, height, fps

    def normalize(self, source: Path, destination: Path) -> Path:
        """Scale and center-crop a source to 420p while preserving its aspect ratio."""
        self._require_files(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        filter_graph = f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,crop={self.width}:{self.height},fps={self.fps},format=yuv420p"
        self._run("-y", "-i", str(source), "-vf", filter_graph, "-c:v", "h264_videotoolbox", "-b:v", "10M", "-movflags", "+faststart", "-an", str(destination))
        return destination

    def concatenate(self, chunks: Sequence[Path], destination: Path) -> Path:
        """Normalize then concatenate chunks through FFmpeg's concat filter."""
        if not chunks:
            raise ValueError("at least one chunk is required")
        self._require_files(*chunks)
        destination.parent.mkdir(parents=True, exist_ok=True)
        inputs = tuple(argument for chunk in chunks for argument in ("-i", str(chunk)))
        normalized = "".join(f"[{index}:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,crop={self.width}:{self.height},fps={self.fps},format=yuv420p[v{index}];" for index in range(len(chunks)))
        concat_inputs = "".join(f"[v{index}]" for index in range(len(chunks)))
        self._run("-y", *inputs, "-filter_complex", f"{normalized}{concat_inputs}concat=n={len(chunks)}:v=1:a=0[outv]", "-map", "[outv]", "-c:v", "h264_videotoolbox", "-b:v", "10M", "-movflags", "+faststart", str(destination))
        return destination

    def add_audio(self, video: Path, audio: Path, destination: Path, fade_seconds: float = 0.5) -> Path:
        """Mux generated audio, applying matching fade-in and fade-out effects."""
        self._require_files(video, audio)
        if fade_seconds < 0:
            raise ValueError("fade_seconds must not be negative")
        duration = self._duration(video)
        fade_out_start = max(0.0, duration - fade_seconds)
        destination.parent.mkdir(parents=True, exist_ok=True)
        audio_filter = f"afade=t=in:st=0:d={fade_seconds},afade=t=out:st={fade_out_start}:d={fade_seconds}"
        self._run("-y", "-i", str(video), "-i", str(audio), "-filter:a", audio_filter, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(destination))
        return destination

    def _duration(self, path: Path) -> float:
        result = subprocess.run([self.ffmpeg_binary.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], check=True, capture_output=True, text=True)
        return float(result.stdout.strip())

    def _run(self, *arguments: str) -> None:
        subprocess.run([self.ffmpeg_binary, *arguments], check=True, capture_output=True, text=True)

    @staticmethod
    def _require_files(*paths: Path) -> None:
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
