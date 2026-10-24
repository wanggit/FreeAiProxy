from __future__ import annotations

import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from freeaiproxy.engine import doubao_selectors as DB
from freeaiproxy.engine import wenxin_selectors as WX
from freeaiproxy.engine.errors import InvalidRequestError, QueueFullError
from freeaiproxy.services.chat_service import ChatService
from freeaiproxy.services.models import get_model_spec

router = APIRouter()


def _normalize_choice(value: str | None, choices: tuple[str, ...], field: str) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    for choice in choices:
        if stripped.lower() == choice.lower():
            return choice
    raise InvalidRequestError(f"{field} must be one of: {', '.join(choices)}")


class _MusicRequest(BaseModel):
    model: str = "wenxin-music"
    prompt: str
    style: str | None = None
    voice: str | None = None
    mood: str | None = None
    lyrics: str | None = None
    model_config = ConfigDict(extra="ignore")


@router.post("/v1/music/generations")
async def create_music(req: _MusicRequest, request: Request):
    if not req.prompt.strip() and not (req.lyrics or "").strip():
        raise InvalidRequestError("prompt or lyrics must not be empty")
    spec = get_model_spec(req.model)
    if spec.kind != "music":
        raise InvalidRequestError(f"model '{spec.id}' does not generate music")
    styles: tuple[str, ...]
    voices: tuple[str, ...]
    moods: tuple[str, ...]
    if spec.provider == "doubao":
        styles, voices, moods = DB.MUSIC_GENRES, DB.MUSIC_VOICES, DB.MUSIC_MOODS
    else:
        styles, voices, moods = WX.MUSIC_STYLES, WX.MUSIC_VOICES, ()
    style = _normalize_choice(req.style, styles, "style")
    voice = _normalize_choice(req.voice, voices, "voice")
    mood = _normalize_choice(req.mood, moods, "mood")
    svc: ChatService | None = request.app.state.services.get(spec.provider)
    if svc is None:
        raise InvalidRequestError(f"provider '{spec.provider}' is disabled")
    if not svc.has_capacity():
        raise QueueFullError(
            f"request queue is full ({svc.active_requests + svc.waiting_requests}"
            f"/{svc.settings.worker.max_queue_len})"
        )

    clip = await svc.generate_music(
        req.prompt,
        spec,
        style=style,
        voice=voice,
        mood=mood,
        lyrics=(req.lyrics or "").strip() or None,
    )
    base = str(request.base_url).rstrip("/")
    return {"created": int(time.time()), "data": [{"url": f"{base}/v1/files/{clip.name}"}]}
