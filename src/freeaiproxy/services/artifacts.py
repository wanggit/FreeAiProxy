from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from freeaiproxy.config import Settings

GENERATED_DIR = Path("data/generated")


@dataclass
class SavedImage:
    name: str
    path: Path


def generated_dir() -> Path:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    return GENERATED_DIR


def new_artifact_path(ext: str) -> Path:
    if not ext.startswith("."):
        ext = "." + ext
    return generated_dir() / f"{uuid.uuid4().hex}{ext}"


def file_url(settings: Settings, name: str) -> str:
    host = settings.server.host
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{settings.server.port}/v1/files/{name}"
