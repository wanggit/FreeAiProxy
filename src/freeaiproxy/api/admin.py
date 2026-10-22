from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request

from freeaiproxy.api.deps import require_api_key
from freeaiproxy.engine.errors import InvalidRequestError

logger = logging.getLogger("freeaiproxy.admin")

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request):
    runtimes = request.app.state.runtimes
    providers = {
        name: {
            "logged_in": rt.bm.logged_in,
            "state": rt.bm.state,
            "active_requests": rt.service.active_requests,
            "waiting_requests": rt.service.waiting_requests,
        }
        for name, rt in runtimes.items()
    }
    return {
        "status": "ok",
        "logged_in": all(p["logged_in"] for p in providers.values()),
        "providers": providers,
    }


@router.get("/admin/status", dependencies=[Depends(require_api_key)])
async def admin_status(request: Request):
    bms = request.app.state.bms
    services = request.app.state.services
    db = request.app.state.db
    settings = request.app.state.settings
    providers: dict = {}
    total_conversations = 0
    for name, bm in bms.items():
        svc = services[name]
        active_conversations = len(await db.list_active_conversations(provider=name))
        total_conversations += active_conversations
        providers[name] = {
            "state": bm.state,
            "logged_in": bm.logged_in,
            "login_waiting": bm.login_waiting,
            "active_requests": svc.active_requests,
            "waiting_requests": svc.waiting_requests,
            "max_queue_len": settings.worker.max_queue_len,
            "active_conversations": active_conversations,
            "queue": svc.queue_stats(),
        }
    default = providers[next(iter(providers))]
    return {
        "state": default["state"],
        "logged_in": default["logged_in"],
        "active_requests": default["active_requests"],
        "waiting_requests": default["waiting_requests"],
        "queue": default["queue"],
        "providers": providers,
        "active_conversations": total_conversations,
        "max_queue_len": settings.worker.max_queue_len,
        "browser_headed": any(bm.headless is False for bm in bms.values()),
        "api_key_enabled": bool(settings.server.api_key),
    }


@router.post("/admin/relogin", dependencies=[Depends(require_api_key)])
async def admin_relogin(request: Request, provider: str = Query("deepseek")):
    bm = request.app.state.bms.get(provider)
    if bm is None:
        raise InvalidRequestError(f"unknown provider '{provider}'")
    if bm.login_waiting:
        return {"started": True, "already_waiting": True, "provider": provider}
    task = asyncio.create_task(bm.relogin())

    def _on_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("%s relogin task failed: %s", provider, exc)

    task.add_done_callback(_on_done)
    return {
        "started": True,
        "provider": provider,
        "message": "complete login in the opened browser window",
    }
