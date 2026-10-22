from __future__ import annotations

import base64
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from freeaiproxy.engine import yuanbao_selectors as YB
from freeaiproxy.engine.errors import InvalidRequestError, QueueFullError
from freeaiproxy.services.chat_service import ChatService
from freeaiproxy.services.models import get_model_spec

router = APIRouter()

_AUTO_RATIOS = ("", "auto", "smart", "default")


def _normalize_ratio(size: str | None) -> str | None:
    if size is None:
        return None
    value = size.strip()
    if value.lower() in _AUTO_RATIOS:
        return None
    for ratio in YB.IMAGE_RATIOS:
        if value.lower() == ratio.lower():
            return ratio
    raise InvalidRequestError(
        "size must be one of the supported aspect ratios "
        f"({', '.join(YB.IMAGE_RATIOS)}) or 'auto'"
    )


class _ImageRequest(BaseModel):
    model: str = "yuanbao-image"
    prompt: str
    n: int = 4
    size: str | None = None
    quality: str | None = None
    style: str | None = None
    response_format: str = "url"
    model_config = ConfigDict(extra="ignore")


@router.post("/v1/images/generations")
async def create_image(req: _ImageRequest, request: Request):
    if not req.prompt.strip():
        raise InvalidRequestError("prompt must not be empty")
    if not 1 <= req.n <= 4:
        raise InvalidRequestError("n must be between 1 and 4")
    if req.response_format not in ("url", "b64_json"):
        raise InvalidRequestError("response_format must be 'url' or 'b64_json'")
    spec = get_model_spec(req.model)
    if spec.kind != "image":
        raise InvalidRequestError(f"model '{spec.id}' does not generate images")
    ratio = _normalize_ratio(req.size)
    svc: ChatService | None = request.app.state.services.get(spec.provider)
    if svc is None:
        raise InvalidRequestError(f"provider '{spec.provider}' is disabled")
    if not svc.has_capacity():
        raise QueueFullError(
            f"request queue is full ({svc.active_requests + svc.waiting_requests}"
            f"/{svc.settings.worker.max_queue_len})"
        )

    images = await svc.generate_images(req.prompt, spec, limit=req.n, ratio=ratio)
    base = str(request.base_url).rstrip("/")
    data = []
    for image in images:
        if req.response_format == "b64_json":
            data.append({"b64_json": base64.b64encode(image.path.read_bytes()).decode()})
        else:
            data.append({"url": f"{base}/v1/files/{image.name}"})
    return {"created": int(time.time()), "data": data}
