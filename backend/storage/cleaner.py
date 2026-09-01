"""Housekeeping for temporary frame batches produced during generation."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from backend.config import settings


def purge_orphaned_frame_batches(max_age_hours: float = 24.0) -> list[Path]:
    """Delete stale temporary frame files and directories, preserving exports and metadata."""
    if max_age_hours < 0:
        raise ValueError("max_age_hours must be non-negative")
    root = settings.temp_frames_dir
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - max_age_hours * 3600
    removed: list[Path] = []
    for path in root.iterdir():
        if path.stat().st_mtime > cutoff:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(path)
    return removed


if __name__ == "__main__":
    for removed_path in purge_orphaned_frame_batches():
        print(removed_path)
