"""Clients for local inference services."""

from backend.inference.comfy_client import ComfyUIClient
from backend.inference.ollama_client import OllamaVisionClient

__all__ = ["ComfyUIClient", "OllamaVisionClient"]
