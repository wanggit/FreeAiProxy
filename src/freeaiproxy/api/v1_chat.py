from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from freeaiproxy.engine.errors import (
    GatewayError,
    InvalidRequestError,
    QueueFullError,
)
from freeaiproxy.services.attachments import AttachmentStore
from freeaiproxy.services.chat_service import ChatService
from freeaiproxy.services.models import get_model_spec
from freeaiproxy.services.session_service import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    Msg,
)

router = APIRouter()

ROLE_MAP = {
    "system": ROLE_SYSTEM,
    "user": ROLE_USER,
    "assistant": ROLE_ASSISTANT,
    "developer": ROLE_SYSTEM,
}


class _ChatMessage(BaseModel):
    role: str
    content: str | list = ""
    model_config = ConfigDict(extra="ignore")

class _ChatCompletionRequest(BaseModel):
    model: str
    messages: list[_ChatMessage]
    stream: bool = False
    session_id: str | None = None
    model_config = ConfigDict(extra="ignore")


def _content_to_text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for p in content:
            if not isinstance(p, dict):
                raise InvalidRequestError("message content parts must be objects")
            ptype = p.get("type")
            if ptype == "text":
                parts.append(str(p.get("text", "")))
            elif ptype in ("image_url", "file", "input_audio"):
                continue
            else:
                raise InvalidRequestError(f"unsupported content part type '{ptype}'")
    return "\n".join(parts)


def _collect_attachments(
    content: str | list, store: AttachmentStore | None
) -> list:
    """Materialise every non-text content part into a local file."""
    if isinstance(content, str) or store is None:
        return []
    out: list = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "image_url":
            spec = part.get("image_url") or {}
            url = spec.get("url") if isinstance(spec, dict) else spec
            if not url:
                raise InvalidRequestError("image_url part is missing its url")
            if str(url).startswith("data:"):
                out.append(store.add_data_url(str(url)))
            else:
                out.append(store.add_remote(str(url)))
        elif ptype == "file":
            spec = part.get("file") or {}
            if not isinstance(spec, dict):
                raise InvalidRequestError("file part must carry a file object")
            filename = str(spec.get("filename") or "attachment")
            data = spec.get("file_data") or spec.get("data")
            if not data:
                raise InvalidRequestError("file part is missing file_data")
            if str(data).startswith("data:"):
                out.append(store.add_data_url(str(data)))
            else:
                out.append(store.add_inline(filename, str(data)))
    return out


def _leading_system(messages: list[_ChatMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        role = ROLE_MAP.get(m.role, m.role)
        if role != ROLE_SYSTEM:
            break
        text = _content_to_text(m.content)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def build_internal_messages(
    messages: list[_ChatMessage], store: AttachmentStore | None = None
) -> tuple[list[Msg], str]:
    out: list[Msg] = []
    for m in messages:
        role = ROLE_MAP.get(m.role)
        if role is None:
            if m.role in ("tool", "function"):
                raise InvalidRequestError(f"role '{m.role}' is not supported")
            raise InvalidRequestError(f"unknown role '{m.role}'")
        if role == ROLE_SYSTEM:
            continue
        text = _content_to_text(m.content)
        attachments = _collect_attachments(m.content, store)
        if role == ROLE_USER and not text.strip() and not attachments:
            raise InvalidRequestError("user message content must not be empty")
        out.append(Msg(role=role, content=text, attachments=attachments))
    if not out:
        raise InvalidRequestError("messages must contain at least one user message")
    return out, _leading_system(messages)


def validate_model(model: str) -> str:
    return get_model_spec(model).id


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _chunk_body(
    resp_id: str,
    model: str,
    created: int,
    delta: dict,
    *,
    finish_reason: str | None = None,
    session_id: str | None = None,
) -> dict:
    body = {
        "id": resp_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if session_id is not None:
        body["session_id"] = session_id
    return body


def _completion_body(resp_id: str, model: str, created: int, content: str, conv_id: str) -> dict:
    return {
        "id": resp_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "session_id": conv_id,
    }


@router.post("/v1/chat/completions")
async def create_chat_completion(req: _ChatCompletionRequest, request: Request):
    spec = get_model_spec(req.model)
    settings = request.app.state.settings
    store = AttachmentStore(settings.attach)
    try:
        internal, sys_text = build_internal_messages(req.messages, store)
        if not settings.attach.enabled and any(m.attachments for m in internal):
            raise InvalidRequestError("attachments are disabled on this server")
        svc: ChatService | None = request.app.state.services.get(spec.provider)
        if svc is None:
            raise InvalidRequestError(f"provider '{spec.provider}' is disabled")
        session_id = request.headers.get("x-session-id") or req.session_id
        res = await svc.prepare(internal, session_id, sys_text, spec)
    except BaseException:
        store.cleanup()
        raise
    return await _serve(svc, req, spec, res, store)


async def _serve(
    svc: ChatService,
    req: _ChatCompletionRequest,
    spec,
    res,
    store: AttachmentStore,
) -> JSONResponse | StreamingResponse:
    if not svc.has_capacity():
        store.cleanup()
        raise QueueFullError(
            f"request queue is full ({svc.active_requests + svc.waiting_requests}"
            f"/{svc.settings.worker.max_queue_len})"
        )
    model = spec.id
    resp_id = "chatcmpl-" + uuid.uuid4().hex[:12]
    created = int(time.time())

    if not req.stream:
        try:
            outcome = await svc.run(res, spec)
        finally:
            store.cleanup()
        body = _completion_body(resp_id, model, created, outcome.content, outcome.conv_id)
        return JSONResponse(content=body, headers={"X-Session-Id": outcome.conv_id})

    stream_res = res

    async def gen():
        try:
            yield _sse(
                _chunk_body(
                    resp_id, model, created, {"role": "assistant"}, session_id=stream_res.conv_id
                )
            )
            async for delta in svc.stream_run(stream_res, spec):
                if delta:
                    yield _sse(_chunk_body(resp_id, model, created, {"content": delta}))
            yield _sse(
                _chunk_body(
                    resp_id,
                    model,
                    created,
                    {},
                    finish_reason="stop",
                    session_id=stream_res.conv_id,
                )
            )
            yield "data: [DONE]\n\n"
        except GatewayError as e:
            try:
                yield _sse(e.to_payload())
                yield "data: [DONE]\n\n"
            except Exception:
                pass
        finally:
            store.cleanup()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "X-Session-Id": stream_res.conv_id,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
