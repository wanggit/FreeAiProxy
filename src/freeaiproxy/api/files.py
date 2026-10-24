from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from freeaiproxy.services.artifacts import GENERATED_DIR

router = APIRouter()

_NAME_RE = re.compile(
    r"^[0-9a-f]{32}\.(png|webp|jpg|pptx|mp3|wav|flac|mp4|webm)$"
)

_PPTX_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

_AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac"}

_VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}


@router.get("/v1/files/{name}")
async def get_generated_file(name: str):
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="file not found")
    path = GENERATED_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        return FileResponse(path, media_type=_PPTX_TYPE, filename=name)
    if suffix in _AUDIO_TYPES or suffix in _VIDEO_TYPES:
        media_type = _AUDIO_TYPES.get(suffix) or _VIDEO_TYPES[suffix]
        return FileResponse(
            path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"}
        )
    media_type = {
        ".webp": "image/webp",
        ".jpg": "image/jpeg",
    }.get(suffix, "image/png")
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"}
    )
