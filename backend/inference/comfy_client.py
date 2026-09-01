"""Async client for submitting and observing headless ComfyUI workflows."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp


class ComfyUIClientError(RuntimeError):
    """Raised when ComfyUI rejects a workflow or cannot provide its outputs."""


@dataclass(frozen=True, slots=True)
class ComfyProgressEvent:
    """One ComfyUI execution event normalized for API consumers."""

    prompt_id: str
    event_type: str
    node_id: str | None = None
    value: int | None = None
    maximum: int | None = None
    message: str | None = None

    @property
    def progress(self) -> float | None:
        """Return node progress as a fraction when ComfyUI supplied both values."""
        if self.value is None or self.maximum in (None, 0):
            return None
        return self.value / self.maximum


@dataclass(frozen=True, slots=True)
class ComfyOutputFile:
    """A generated image, video frame, or latent file made available by ComfyUI."""

    filename: str
    subfolder: str
    folder_type: str
    data: bytes


class ComfyUIClient:
    """Submit ComfyUI API workflows and retrieve their generated artifacts."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        timeout_seconds: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def submit_workflow(
        self,
        workflow: dict[str, Any],
        client_id: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> tuple[str, str]:
        """Submit an API-format workflow and return its prompt and client identifiers."""
        if not workflow:
            raise ValueError("workflow must not be empty")
        resolved_client_id = client_id or str(uuid4())
        payload = {"prompt": workflow, "client_id": resolved_client_id}
        if session is not None:
            prompt_id = await self._post_prompt(session, payload)
        else:
            async with aiohttp.ClientSession(timeout=self.timeout) as owned_session:
                prompt_id = await self._post_prompt(owned_session, payload)
        return prompt_id, resolved_client_id

    async def monitor_workflow(
        self,
        prompt_id: str,
        client_id: str,
        session: aiohttp.ClientSession | None = None,
    ) -> AsyncIterator[ComfyProgressEvent]:
        """Yield WebSocket execution progress until this prompt succeeds or fails."""
        if session is not None:
            async for event in self._monitor(session, prompt_id, client_id):
                yield event
            return
        async with aiohttp.ClientSession(timeout=self.timeout) as owned_session:
            async for event in self._monitor(owned_session, prompt_id, client_id):
                yield event

    async def retrieve_outputs(
        self,
        prompt_id: str,
        session: aiohttp.ClientSession | None = None,
    ) -> list[ComfyOutputFile]:
        """Fetch all downloadable files reported by a completed workflow's history."""
        if session is not None:
            return await self._retrieve_outputs(session, prompt_id)
        async with aiohttp.ClientSession(timeout=self.timeout) as owned_session:
            return await self._retrieve_outputs(owned_session, prompt_id)

    async def download_output(
        self,
        filename: str,
        subfolder: str = "",
        folder_type: str = "output",
        session: aiohttp.ClientSession | None = None,
    ) -> bytes:
        """Download a named ComfyUI output, including frames or latent artifacts."""
        if session is not None:
            return await self._download_output(session, filename, subfolder, folder_type)
        async with aiohttp.ClientSession(timeout=self.timeout) as owned_session:
            return await self._download_output(owned_session, filename, subfolder, folder_type)

    async def save_outputs(self, prompt_id: str, destination: Path) -> list[Path]:
        """Retrieve workflow files and persist them below a local destination directory."""
        outputs = await self.retrieve_outputs(prompt_id)
        saved_paths: list[Path] = []
        for output in outputs:
            relative_path = Path(output.subfolder) / output.filename
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ComfyUIClientError(f"Unsafe ComfyUI output path: {relative_path}")
            target = destination / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(output.data)
            saved_paths.append(target)
        return saved_paths

    async def _post_prompt(self, session: aiohttp.ClientSession, payload: dict[str, Any]) -> str:
        data = await self._request_json(session, "POST", "/prompt", json_payload=payload)
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyUIClientError(f"ComfyUI did not return a prompt_id: {data}")
        return prompt_id

    async def _monitor(
        self, session: aiohttp.ClientSession, prompt_id: str, client_id: str
    ) -> AsyncIterator[ComfyProgressEvent]:
        websocket_url = self.base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        try:
            async with session.ws_connect(f"{websocket_url}/ws", params={"clientId": client_id}) as websocket:
                async for message in websocket:
                    if message.type is aiohttp.WSMsgType.ERROR:
                        raise ComfyUIClientError(f"ComfyUI WebSocket error: {websocket.exception()}")
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    event = self._parse_event(message.data, prompt_id)
                    if event is None:
                        continue
                    yield event
                    if event.event_type == "execution_error":
                        raise ComfyUIClientError(event.message or "ComfyUI workflow failed")
                    if event.event_type == "execution_success":
                        return
        except aiohttp.ClientError as error:
            raise ComfyUIClientError(f"Could not monitor ComfyUI at {self.base_url}: {error}") from error

    @staticmethod
    def _parse_event(raw_event: str, prompt_id: str) -> ComfyProgressEvent | None:
        try:
            envelope = json.loads(raw_event)
            event_type = envelope.get("type")
            data = envelope.get("data", {})
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or data.get("prompt_id") != prompt_id:
            return None
        node_id = data.get("node")
        if node_id is not None:
            node_id = str(node_id)
        if event_type == "progress":
            return ComfyProgressEvent(prompt_id, event_type, node_id, data.get("value"), data.get("max"))
        if event_type == "executing" and data.get("node") is None:
            return ComfyProgressEvent(prompt_id, "execution_success")
        if event_type == "execution_error":
            return ComfyProgressEvent(prompt_id, event_type, node_id, message=str(data.get("exception_message", "Workflow failed")))
        if event_type in {"execution_start", "executing", "executed", "execution_cached"}:
            return ComfyProgressEvent(prompt_id, event_type, node_id)
        return None

    async def _retrieve_outputs(self, session: aiohttp.ClientSession, prompt_id: str) -> list[ComfyOutputFile]:
        history = await self._request_json(session, "GET", f"/history/{prompt_id}")
        record = history.get(prompt_id)
        if not isinstance(record, dict):
            raise ComfyUIClientError(f"No completed ComfyUI history found for prompt {prompt_id}")
        files = self._find_file_descriptors(record.get("outputs", {}))
        return await asyncio.gather(
            *(
                self._as_output_file(session, descriptor)
                for descriptor in files
            )
        )

    async def _as_output_file(
        self, session: aiohttp.ClientSession, descriptor: dict[str, str]
    ) -> ComfyOutputFile:
        return ComfyOutputFile(
            filename=descriptor["filename"],
            subfolder=descriptor["subfolder"],
            folder_type=descriptor["type"],
            data=await self._download_output(
                session, descriptor["filename"], descriptor["subfolder"], descriptor["type"]
            ),
        )

    @classmethod
    def _find_file_descriptors(cls, value: Any) -> list[dict[str, str]]:
        descriptors: list[dict[str, str]] = []
        if isinstance(value, dict):
            if isinstance(value.get("filename"), str):
                descriptors.append(
                    {
                        "filename": value["filename"],
                        "subfolder": str(value.get("subfolder", "")),
                        "type": str(value.get("type", "output")),
                    }
                )
            else:
                for nested_value in value.values():
                    descriptors.extend(cls._find_file_descriptors(nested_value))
        elif isinstance(value, list):
            for nested_value in value:
                descriptors.extend(cls._find_file_descriptors(nested_value))
        return descriptors

    async def _download_output(
        self, session: aiohttp.ClientSession, filename: str, subfolder: str, folder_type: str
    ) -> bytes:
        try:
            async with session.get(
                f"{self.base_url}/view",
                params={"filename": filename, "subfolder": subfolder, "type": folder_type},
            ) as response:
                body = await response.read()
                if response.status >= 400:
                    raise ComfyUIClientError(f"ComfyUI returned HTTP {response.status}: {body.decode(errors='replace')}")
                return body
        except aiohttp.ClientError as error:
            raise ComfyUIClientError(f"Could not download ComfyUI output: {error}") from error

    async def _request_json(
        self, session: aiohttp.ClientSession, method: str, path: str, json_payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with session.request(method, f"{self.base_url}{path}", json=json_payload) as response:
                body = await response.text()
                if response.status >= 400:
                    raise ComfyUIClientError(f"ComfyUI returned HTTP {response.status}: {body}")
        except aiohttp.ClientError as error:
            raise ComfyUIClientError(f"Could not reach ComfyUI at {self.base_url}: {error}") from error
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as error:
            raise ComfyUIClientError(f"ComfyUI returned invalid JSON: {body}") from error
        if not isinstance(decoded, dict):
            raise ComfyUIClientError("ComfyUI JSON response must be an object")
        return decoded
