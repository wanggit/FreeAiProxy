from __future__ import annotations

import base64
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from freeaiproxy.engine import doubao_selectors as D
from freeaiproxy.engine.errors import InvalidRequestError, QueueFullError
from freeaiproxy.services.chat_service import ChatService
from freeaiproxy.services.models import get_model_spec

router = APIRouter()

_AUTO_RATIOS = ("", "auto", "smart", "default")


def _normalize_ratio(size: str | None, provider: str) -> str | None:
    if size is None:
        return None
    value = size.strip()
    if value.lower() in _AUTO_RATIOS:
        return None
    if provider == "doubao":
        for ratio in D.VIDEO_RATIOS:
            if value.lower() == ratio.lower():
                return ratio
        raise InvalidRequestError(
            "size must be one of the supported aspect ratios "
            f"({', '.join(D.VIDEO_RATIOS)}) or 'auto'"
        )
    return value


def _normalize_duration(duration: int | None) -> int | None:
    if duration is None:
        return None
    if not D.VIDEO_DURATION_MIN <= duration <= D.VIDEO_DURATION_MAX:
        raise InvalidRequestError(
            "duration must be between "
            f"{D.VIDEO_DURATION_MIN} and {D.VIDEO_DURATION_MAX} seconds"
        )
    return duration


class _VideoRequest(BaseModel):
    model: str = "doubao-video"
    prompt: str
    n: int = 1
    size: str | None = None
    duration: int | None = None
    seconds: int | None = None
    response_format: str = "url"
    model_config = ConfigDict(extra="ignore")


@router.post("/v1/videos/generations")
async def create_video(req: _VideoRequest, request: Request):
    if not req.prompt.strip():
        raise InvalidRequestError("prompt must not be empty")
    if not 1 <= req.n <= 4:
        raise InvalidRequestError("n must be between 1 and 4")
    if req.response_format not in ("url", "b64_json"):
        raise InvalidRequestError("response_format must be 'url' or 'b64_json'")
    spec = get_model_spec(req.model)
    if spec.kind != "video":
        raise InvalidRequestError(f"model '{spec.id}' does not generate videos")
    ratio = _normalize_ratio(req.size, spec.provider)
    duration = _normalize_duration(req.duration if req.duration is not None else req.seconds)
    svc: ChatService | None = request.app.state.services.get(spec.provider)
    if svc is None:
        raise InvalidRequestError(f"provider '{spec.provider}' is disabled")
    if not svc.has_capacity():
        raise QueueFullError(
            f"request queue is full ({svc.active_requests + svc.waiting_requests}"
            f"/{svc.settings.worker.max_queue_len})"
        )

    clip = await svc.generate_video(
        req.prompt, spec, limit=req.n, ratio=ratio, duration=duration
    )
    base = str(request.base_url).rstrip("/")
    if req.response_format == "b64_json":
        payload = {"b64_json": base64.b64encode(clip.path.read_bytes()).decode()}
    else:
        payload = {"url": f"{base}/v1/files/{clip.name}"}
    return {"created": int(time.time()), "data": [payload]}
