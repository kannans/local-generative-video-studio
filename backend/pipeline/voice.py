"""Local text-to-speech after silent video, then FFmpeg mux."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from backend.config import Settings, settings
from backend.inference.comfy_client import ComfyProgressEvent, ComfyUIClientError
from backend.pipeline.composer import VideoComposer
from backend.pipeline.t2v import GeneratedVideo

logger = logging.getLogger(__name__)

VoiceModel = Literal["off", "kokoro", "qwen-base"]
VoiceGender = Literal["female", "male"]

KOKORO_MODEL = "mlx-community/Kokoro-82M-bf16"
QWEN_BASE_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
KOKORO_VOICES = {"female": "af_heart", "male": "am_adam"}
REFERENCE_FILES = {"female": "female_reference.wav", "male": "male_reference.wav"}

_load_lock = threading.Lock()
_loaded_id: str | None = None
_loaded_model: Any = None


@dataclass(frozen=True, slots=True)
class VoiceRequest:
    """Studio controls for optional voiceover on a generated clip."""

    model: VoiceModel = "off"
    gender: VoiceGender = "female"
    script: str = ""

    def __post_init__(self) -> None:
        if self.model not in {"off", "kokoro", "qwen-base"}:
            raise ValueError("voice model must be off, kokoro, or qwen-base")


@dataclass(frozen=True, slots=True)
class VoiceStatus:
    """Installed TTS engines and on-disk male/female reference clips."""

    available: tuple[str, ...]
    optional: tuple[str, ...]
    default: str
    references: dict[str, bool]
    install_hint: str


def _hub_snapshot_exists(repo_id: str) -> bool:
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    directory = cache / ("models--" + repo_id.replace("/", "--")) / "snapshots"
    return directory.is_dir() and any(directory.iterdir())


def _mlx_audio_available() -> bool:
    try:
        import mlx_audio  # noqa: F401
    except ImportError:
        return False
    return True


def reference_path(gender: VoiceGender, app_settings: Settings = settings) -> Path:
    return app_settings.voices_dir / REFERENCE_FILES[gender]


def voice_status(app_settings: Settings = settings) -> VoiceStatus:
    """Report which voice engines the studio can run without a download."""
    available = ["off"]
    if _mlx_audio_available():
        available.append("kokoro")
    if _hub_snapshot_exists(QWEN_BASE_MODEL) or _hub_snapshot_exists(
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
    ):
        available.append("qwen-base")
    references = {
        gender: reference_path(gender, app_settings).is_file() for gender in ("female", "male")
    }
    return VoiceStatus(
        available=tuple(available),
        optional=("qwen-base",),
        default="kokoro" if "kokoro" in available else "off",
        references=references,
        install_hint="Install mlx-audio for Kokoro. See README Voice section for Qwen3-TTS Base and reference WAVs.",
    )


def _script_from(request: VoiceRequest, prompt: str) -> str:
    text = request.script.strip() or prompt.strip()
    if len(text) <= 240:
        return text
    cutoff = text[:240]
    sentence = cutoff.rsplit(".", 1)[0]
    return (sentence + ".") if sentence else cutoff


def _unload_locked() -> None:
    global _loaded_id, _loaded_model
    _loaded_id = None
    _loaded_model = None


def _load_model_locked(model_id: str) -> Any:
    global _loaded_id, _loaded_model
    if _loaded_id == model_id and _loaded_model is not None:
        return _loaded_model
    from mlx_audio.tts.utils import load_model

    logger.info("Loading TTS model %s", model_id)
    _unload_locked()
    _loaded_model = load_model(model_id)
    _loaded_id = model_id
    return _loaded_model


def _reference_transcript(reference: Path) -> str:
    transcript = reference.with_suffix(".txt")
    if transcript.is_file():
        text = transcript.read_text(encoding="utf-8").strip()
        if text:
            return text
    return "The tide draws a silver line across the black sand at dawn."


def _write_audio(destination: Path, audio: Any, sample_rate: int) -> None:
    import numpy as np
    import soundfile as sf

    destination.parent.mkdir(parents=True, exist_ok=True)
    values = audio.tolist() if hasattr(audio, "tolist") else audio
    array = np.array(values, dtype=np.float32)
    if array.ndim > 1:
        array = array.reshape(-1)
    sf.write(destination, array, sample_rate)


def _collect_audio(results: Any) -> tuple[Any, int]:
    items = list(results)
    if not items:
        raise ComfyUIClientError("TTS model returned no audio")
    chunks: list[Any] = []
    rate = 24000
    for item in items:
        audio = getattr(item, "audio", item)
        rate = int(getattr(item, "sample_rate", None) or getattr(item, "sampling_rate", rate) or rate)
        chunks.append(audio)
    if len(chunks) == 1:
        return chunks[0], rate
    import numpy as np

    arrays = [
        np.array(chunk.tolist() if hasattr(chunk, "tolist") else chunk, dtype=np.float32).reshape(-1)
        for chunk in chunks
    ]
    return np.concatenate(arrays), rate


def _synthesize_kokoro(text: str, gender: VoiceGender, destination: Path) -> Path:
    with _load_lock:
        model = _load_model_locked(KOKORO_MODEL)
        voice = KOKORO_VOICES[gender]
        generate = getattr(model, "generate", None)
        if generate is None:
            raise ComfyUIClientError("Kokoro model does not expose generate()")
        try:
            results = generate(text=text, voice=voice)
        except TypeError:
            results = generate(text, voice=voice)
        audio, rate = _collect_audio(results)
        _write_audio(destination, audio, rate)
    return destination


def _synthesize_qwen(text: str, reference: Path, destination: Path) -> Path:
    with _load_lock:
        model_id = QWEN_BASE_MODEL if _hub_snapshot_exists(QWEN_BASE_MODEL) else "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
        model = _load_model_locked(model_id)
        generate = getattr(model, "generate", None)
        if generate is None:
            raise ComfyUIClientError("Qwen3-TTS Base does not expose a generate method")
        results = generate(
            text=text,
            ref_audio=str(reference),
            ref_text=_reference_transcript(reference),
        )
        audio, rate = _collect_audio(results)
        _write_audio(destination, audio, rate)
    return destination


class VoiceEngine:
    """Generate a voiceover after video and mux it onto the export."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self.composer = VideoComposer()

    def status(self) -> VoiceStatus:
        return voice_status(self.settings)

    def synthesize(self, request: VoiceRequest, prompt: str, run_id: str) -> Path:
        """Render a WAV for the selected engine. Loads only that one model."""
        if request.model == "off":
            raise ValueError("voice model is off")
        status = self.status()
        if request.model not in status.available:
            raise ComfyUIClientError(
                f"Voice model '{request.model}' is not installed. {status.install_hint}"
            )
        text = _script_from(request, prompt)
        if not text:
            raise ValueError("voice script must not be empty")
        destination = self.settings.exports_dir / run_id / "voice.wav"
        if request.model == "kokoro":
            return _synthesize_kokoro(text, request.gender, destination)
        reference = reference_path(request.gender, self.settings)
        if not reference.is_file():
            raise ComfyUIClientError(
                f"Add a {request.gender} reference WAV at {reference} before using Natural voice."
            )
        return _synthesize_qwen(text, reference, destination)

    async def attach(
        self,
        generated: GeneratedVideo,
        request: VoiceRequest,
        progress_callback: Callable[[ComfyProgressEvent], Awaitable[None]] | None = None,
    ) -> GeneratedVideo:
        """Mux generated speech onto a completed silent clip."""
        if request.model == "off":
            return generated
        if progress_callback is not None:
            await progress_callback(
                ComfyProgressEvent(
                    prompt_id=generated.prompt_id,
                    event_type="generating_voice",
                    node_id="voice",
                    value=92,
                    maximum=100,
                    message="Generating voiceover",
                )
            )
        wav = await asyncio.to_thread(self.synthesize, request, generated.request.prompt, generated.run_id)
        muxed = generated.video_path.with_name("video_voice.mp4")
        self.composer.add_audio(generated.video_path, wav, muxed)
        return replace(generated, video_path=muxed, files=(*generated.files, wav, muxed))
