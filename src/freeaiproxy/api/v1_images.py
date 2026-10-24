from __future__ import annotations

import base64
import time

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict

from freeaiproxy.engine import doubao_selectors as DB
from freeaiproxy.engine import yuanbao_selectors as YB
from freeaiproxy.engine.errors import InvalidRequestError, QueueFullError
from freeaiproxy.services.attachments import Attachment, AttachmentStore
from freeaiproxy.services.chat_service import ChatService
from freeaiproxy.services.models import ModelSpec, get_model_spec

router = APIRouter()

_AUTO_RATIOS = ("", "auto", "smart", "default")

_REF_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
# Reference-image ceilings verified end-to-end against the live web UIs
# (2026-10-24). The sites accept more (yuanbao 5, doubao 10) but multi-file
# upload through this gateway is only calibrated for one image per request.
_REF_IMAGE_LIMITS = {"yuanbao": 1, "doubao": 1, "chatglm": 1}


def _normalize_ratio(size: str | None, provider: str) -> str | None:
    if size is None:
        return None
    value = size.strip()
    if value.lower() in _AUTO_RATIOS:
        return None
    if provider == "yuanbao":
        for ratio in YB.IMAGE_RATIOS:
            if value.lower() == ratio.lower():
                return ratio
        raise InvalidRequestError(
            "size must be one of the supported aspect ratios "
            f"({', '.join(YB.IMAGE_RATIOS)}) or 'auto'"
        )
    if provider == "doubao":
        for ratio in DB.IMAGE_RATIOS:
            if value.lower() == ratio.lower():
                return ratio
        raise InvalidRequestError(
            "size must be one of the supported aspect ratios "
            f"({', '.join(DB.IMAGE_RATIOS)}) or 'auto'"
        )
    # Other providers (e.g. wenxin) have their own ratio vocabulary that is
    # resolved by the driver during calibration; pass the value through.
    return value


def _normalize_style(style: str | None, spec: ModelSpec) -> str | None:
    if spec.provider != "doubao" or style is None or not style.strip():
        return style
    style = style.strip()
    for name in DB.IMAGE_STYLES:
        if style.lower() == name.lower():
            return name
    raise InvalidRequestError("style must be one of: " + ", ".join(DB.IMAGE_STYLES))


def _validate_common(
    prompt: str, n: int, response_format: str, model: str
) -> ModelSpec:
    if not prompt.strip():
        raise InvalidRequestError("prompt must not be empty")
    if not 1 <= n <= 4:
        raise InvalidRequestError("n must be between 1 and 4")
    if response_format not in ("url", "b64_json"):
        raise InvalidRequestError("response_format must be 'url' or 'b64_json'")
    spec = get_model_spec(model)
    if spec.kind != "image":
        raise InvalidRequestError(f"model '{spec.id}' does not generate images")
    return spec


def _require_service(request: Request, spec: ModelSpec) -> ChatService:
    svc: ChatService | None = request.app.state.services.get(spec.provider)
    if svc is None:
        raise InvalidRequestError(f"provider '{spec.provider}' is disabled")
    if not svc.has_capacity():
        raise QueueFullError(
            f"request queue is full ({svc.active_requests + svc.waiting_requests}"
            f"/{svc.settings.worker.max_queue_len})"
        )
    return svc


def _check_reference_support(request: Request, spec: ModelSpec, count: int) -> None:
    if not spec.ref_image:
        raise InvalidRequestError(
            f"model '{spec.id}' does not accept reference images"
        )
    settings = request.app.state.settings
    if not settings.attach.enabled:
        raise InvalidRequestError("reference images are disabled on this server")
    limit = min(_REF_IMAGE_LIMITS.get(spec.provider, 1), settings.attach.max_files)
    if count > limit:
        raise InvalidRequestError(
            f"at most {limit} reference image(s) are supported for '{spec.id}'"
        )


def _accept_image_attachment(att: Attachment) -> Attachment:
    if not str(att.media_type).startswith("image/"):
        raise InvalidRequestError(
            f"reference image '{att.filename}' is not an image ({att.media_type})"
        )
    if att.path.suffix.lower() not in _REF_IMAGE_EXTS:
        raise InvalidRequestError(
            "reference images must be one of: " + ", ".join(sorted(_REF_IMAGE_EXTS))
        )
    return att


def _refs_from_urls(store: AttachmentStore, urls: list[str]) -> list[Attachment]:
    refs: list[Attachment] = []
    for url in urls:
        value = url.strip()
        if not value:
            raise InvalidRequestError("reference image URL must not be empty")
        if value.startswith("data:"):
            refs.append(_accept_image_attachment(store.add_data_url(value)))
        elif value.startswith("http://") or value.startswith("https://"):
            refs.append(_accept_image_attachment(store.add_remote(value)))
        else:
            raise InvalidRequestError(
                "reference images must be a data URL or an http(s) URL"
            )
    return refs


async def _refs_from_uploads(
    store: AttachmentStore, uploads: list[UploadFile]
) -> list[Attachment]:
    refs: list[Attachment] = []
    for i, upload in enumerate(uploads, 1):
        raw = await upload.read()
        if not raw:
            raise InvalidRequestError(f"uploaded reference image #{i} is empty")
        name = upload.filename or f"reference_{i}.png"
        media = (upload.content_type or "").split(";")[0].strip()
        att = store.add_bytes(name, raw)
        if media and not media.startswith("image/"):
            raise InvalidRequestError(
                f"reference image '{att.filename}' is not an image ({media})"
            )
        refs.append(_accept_image_attachment(att))
    return refs


def _build_response(
    request: Request, images: list, response_format: str
) -> dict:
    base = str(request.base_url).rstrip("/")
    data = []
    for image in images:
        if response_format == "b64_json":
            data.append({"b64_json": base64.b64encode(image.path.read_bytes()).decode()})
        else:
            data.append({"url": f"{base}/v1/files/{image.name}"})
    return {"created": int(time.time()), "data": data}


class _ImageRequest(BaseModel):
    model: str = "yuanbao-image"
    prompt: str
    n: int = 4
    size: str | None = None
    quality: str | None = None
    style: str | None = None
    response_format: str = "url"
    image: str | list[str] | None = None
    images: list[str] | None = None
    model_config = ConfigDict(extra="ignore")


def _collect_ref_urls(req: _ImageRequest) -> list[str]:
    urls: list[str] = []
    if req.image is not None:
        urls += [req.image] if isinstance(req.image, str) else list(req.image)
    if req.images is not None:
        urls += list(req.images)
    return urls


@router.post("/v1/images/generations")
async def create_image(req: _ImageRequest, request: Request):
    spec = _validate_common(req.prompt, req.n, req.response_format, req.model)
    ratio = _normalize_ratio(req.size, spec.provider)
    style = _normalize_style(req.style, spec)
    svc = _require_service(request, spec)

    ref_urls = _collect_ref_urls(req)
    store: AttachmentStore | None = None
    refs: list[Attachment] = []
    try:
        if ref_urls:
            _check_reference_support(request, spec, len(ref_urls))
            store = AttachmentStore(request.app.state.settings.attach)
            refs = _refs_from_urls(store, ref_urls)
        images = await svc.generate_images(
            req.prompt, spec, limit=req.n, ratio=ratio, style=style, attachments=refs
        )
        return _build_response(request, images, req.response_format)
    finally:
        if store is not None:
            store.cleanup()


@router.post("/v1/images/edits")
async def edit_image(
    request: Request,
    prompt: str = Form(...),
    model: str = Form("yuanbao-image"),
    n: int = Form(4),
    size: str | None = Form(None),
    style: str | None = Form(None),
    response_format: str = Form("url"),
    image: list[UploadFile] | None = File(None),
    image_multi: list[UploadFile] | None = File(None, alias="image[]"),
    mask: UploadFile | None = File(None),
):
    spec = _validate_common(prompt, n, response_format, model)
    uploads = [u for u in (image or []) + (image_multi or []) if u is not None]
    if not uploads:
        raise InvalidRequestError("edits requires at least one reference image")
    ratio = _normalize_ratio(size, spec.provider)
    style = _normalize_style(style, spec)
    svc = _require_service(request, spec)

    _check_reference_support(request, spec, len(uploads))
    store = AttachmentStore(request.app.state.settings.attach)
    try:
        refs = await _refs_from_uploads(store, uploads)
        images = await svc.generate_images(
            prompt, spec, limit=n, ratio=ratio, style=style, attachments=refs
        )
        return _build_response(request, images, response_format)
    finally:
        store.cleanup()
