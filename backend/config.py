"""Application settings and filesystem locations."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Environment-backed settings for the video generation service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path = Field(default=PROJECT_ROOT, alias="PROJECT_ROOT")
    model_checkpoints_dir: Path = Field(
        default=PROJECT_ROOT / "backend" / "storage" / "model_checkpoints",
        alias="MODEL_CHECKPOINTS_DIR",
    )
    temp_frames_dir: Path = Field(
        default=PROJECT_ROOT / "backend" / "storage" / "temp_frames",
        alias="TEMP_FRAMES_DIR",
    )
    latents_dir: Path = Field(
        default=PROJECT_ROOT / "backend" / "storage" / "latents",
        alias="LATENTS_DIR",
    )
    exports_dir: Path = Field(
        default=PROJECT_ROOT / "backend" / "storage" / "exports",
        alias="EXPORTS_DIR",
    )
    video_width: int = Field(default=746, alias="VIDEO_WIDTH", ge=1)
    video_height: int = Field(default=420, alias="VIDEO_HEIGHT", ge=1)
    frame_rate: int = Field(default=24, alias="FRAME_RATE", ge=1)
    generation_width: int = Field(default=512, alias="GENERATION_WIDTH", ge=16)
    generation_height: int = Field(default=288, alias="GENERATION_HEIGHT", ge=16)
    generation_frames: int = Field(default=49, alias="GENERATION_FRAMES", ge=1, le=144)
    generation_steps: int = Field(default=12, alias="GENERATION_STEPS", ge=1)
    generation_cfg: float = Field(default=5.0, alias="GENERATION_CFG", gt=0)
    generation_fps: int = Field(default=16, alias="GENERATION_FPS", ge=1)
    generation_seed: int = Field(default=73, alias="GENERATION_SEED", ge=0)
    ollama_api_url: str = Field(
        default="http://localhost:11434",
        alias="OLLAMA_API_URL",
    )

    @property
    def resolution(self) -> str:
        """Return the configured video resolution in width-by-height form."""
        return f"{self.video_width}x{self.video_height}"

    @property
    def storage_directories(self) -> tuple[Path, ...]:
        """Return directories that must exist before serving requests."""
        return (
            self.model_checkpoints_dir,
            self.temp_frames_dir,
            self.latents_dir,
            self.exports_dir,
        )


settings = Settings()
