"""Async client for using Ollama's vision-language model as a prompt director."""

import json
from dataclasses import dataclass
from typing import Any

import aiohttp


class OllamaClientError(RuntimeError):
    """Raised when Ollama cannot return a usable prompt-direction response."""


@dataclass(frozen=True, slots=True)
class PromptDirection:
    """Diffusion and audio direction generated from a studio prompt."""

    enhanced_prompt: str
    negative_prompt: str
    camera_motion: str
    mood_audio_prompt: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "PromptDirection":
        """Validate a model response against the prompt-direction contract."""
        fields = (
            "enhanced_prompt",
            "negative_prompt",
            "camera_motion",
            "mood_audio_prompt",
        )
        missing_or_invalid = [
            field for field in fields if not isinstance(value.get(field), str) or not value[field].strip()
        ]
        if missing_or_invalid:
            raise OllamaClientError(
                "Ollama response is missing non-empty string fields: "
                + ", ".join(missing_or_invalid)
            )
        return cls(**{field: value[field].strip() for field in fields})

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-ready representation."""
        return {
            "enhanced_prompt": self.enhanced_prompt,
            "negative_prompt": self.negative_prompt,
            "camera_motion": self.camera_motion,
            "mood_audio_prompt": self.mood_audio_prompt,
        }


class OllamaVisionClient:
    """Generate structured video direction with the local qwen2.5vl model."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5vl:7b",
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def enhance_prompt(
        self,
        prompt: str,
        prior_frame_context: str | dict[str, Any] | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> PromptDirection:
        """Turn a user prompt and optional preceding-frame context into direction."""
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        payload = {
            "model": self.model,
            "prompt": self._instruction(prompt, prior_frame_context),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.25},
        }
        if session is not None:
            return await self._request(session, payload)
        async with aiohttp.ClientSession(timeout=self.timeout) as owned_session:
            return await self._request(owned_session, payload)

    async def _request(
        self, session: aiohttp.ClientSession, payload: dict[str, Any]
    ) -> PromptDirection:
        try:
            async with session.post(f"{self.base_url}/api/generate", json=payload) as response:
                response_body = await response.text()
                if response.status >= 400:
                    raise OllamaClientError(
                        f"Ollama returned HTTP {response.status}: {response_body}"
                    )
        except aiohttp.ClientError as error:
            raise OllamaClientError(f"Could not reach Ollama at {self.base_url}: {error}") from error

        try:
            generated = json.loads(response_body)
            response_text = generated["response"]
            direction = self._decode_json_object(response_text)
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise OllamaClientError("Ollama returned an invalid generate response") from error
        return PromptDirection.from_mapping(direction)

    @staticmethod
    def _instruction(prompt: str, prior_frame_context: str | dict[str, Any] | None) -> str:
        context = "No preceding frame context was supplied."
        if prior_frame_context is not None:
            context = json.dumps(prior_frame_context) if isinstance(prior_frame_context, dict) else prior_frame_context
        return (
            "You are a video diffusion prompt director. Return only one JSON object with "
            "the exact string keys enhanced_prompt, negative_prompt, camera_motion, and "
            "mood_audio_prompt. Make enhanced_prompt visually specific and continuity-aware; "
            "make negative_prompt useful for diffusion; use a concise camera movement such as "
            "static, pan left, dolly in, or zoom out; make mood_audio_prompt describe diegetic "
            "and musical sound design.\n"
            f"User prompt: {prompt}\n"
            f"Preceding frame context: {context}"
        )

    @staticmethod
    def _decode_json_object(response_text: Any) -> dict[str, Any]:
        if not isinstance(response_text, str):
            raise OllamaClientError("Ollama response field is not text")
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        decoded = json.loads(cleaned)
        if not isinstance(decoded, dict):
            raise OllamaClientError("Ollama response JSON must be an object")
        return decoded
