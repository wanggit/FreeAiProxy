from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from freeaiproxy.api.admin import router as admin_router
from freeaiproxy.api.deps import require_api_key
from freeaiproxy.api.files import router as files_router
from freeaiproxy.api.middleware import JsonCharsetMiddleware
from freeaiproxy.api.v1_chat import router as chat_router
from freeaiproxy.api.v1_images import router as images_router
from freeaiproxy.api.v1_models import router as models_router
from freeaiproxy.api.v1_music import router as music_router
from freeaiproxy.config import Settings, enabled_provider_names, load_settings
from freeaiproxy.db import Database
from freeaiproxy.engine.errors import GatewayError
from freeaiproxy.services.provider_runtime import ProviderRuntime, create_runtime

logger = logging.getLogger("freeaiproxy.app")


def create_app(config_path: Path | None = None, settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = settings if settings is not None else load_settings(config_path)
        for name in ("traces", "exports", "generated"):
            (Path("data") / name).mkdir(parents=True, exist_ok=True)
        db = Database(Path("data/freeaiproxy.db"))
        await db.connect()
        runtimes: list[ProviderRuntime] = []
        try:
            names = enabled_provider_names(cfg)
            if not names:
                raise RuntimeError("no providers are enabled in config.toml")
            runtimes = [create_runtime(cfg, name, db) for name in names]
            await asyncio.gather(*(rt.bm.start() for rt in runtimes))

            app.state.settings = cfg
            app.state.db = db
            app.state.runtimes = {rt.name: rt for rt in runtimes}
            app.state.bms = {rt.name: rt.bm for rt in runtimes}
            app.state.services = {rt.name: rt.service for rt in runtimes}
            logger.info("FreeAiProxy ready (providers=%s)", ", ".join(names))
            yield
        finally:
            for rt in runtimes:
                await rt.bm.stop()
            await db.close()

    app = FastAPI(title="FreeAiProxy", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Session-Id"],
    )
    app.add_middleware(JsonCharsetMiddleware)
    app.include_router(chat_router, dependencies=[Depends(require_api_key)])
    app.include_router(models_router, dependencies=[Depends(require_api_key)])
    app.include_router(images_router, dependencies=[Depends(require_api_key)])
    app.include_router(music_router, dependencies=[Depends(require_api_key)])
    app.include_router(files_router)
    app.include_router(admin_router)

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(_request: Request, exc: GatewayError):
        return JSONResponse(exc.to_payload(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        messages = "; ".join(
            f"{'.'.join(str(loc) for loc in e.get('loc', []))}: {e.get('msg', '')}"
            for e in exc.errors()[:3]
        )
        payload = {
            "error": {
                "message": f"invalid request body: {messages}",
                "type": "invalid_request_error",
                "param": None,
                "code": "invalid_request",
            }
        }
        return JSONResponse(payload, status_code=400)

    @app.get("/")
    async def root():
        return {"service": "FreeAiProxy", "version": "0.2.0", "docs": "/docs"}

    return app
