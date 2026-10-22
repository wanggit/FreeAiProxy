from __future__ import annotations

from fastapi import Request

from freeaiproxy.engine.errors import UnauthorizedError


def require_api_key(request: Request) -> None:
    key = request.app.state.settings.server.api_key
    if not key:
        return
    provided = request.headers.get("authorization", "")
    if provided != f"Bearer {key}":
        raise UnauthorizedError("missing or invalid API key (Authorization: Bearer <key>)")
