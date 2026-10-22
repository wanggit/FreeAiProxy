from __future__ import annotations

from fastapi import APIRouter, Request

from freeaiproxy.services.models import MODEL_SPECS

router = APIRouter()


@router.get("/v1/models")
async def list_models(request: Request):
    available = set(request.app.state.services)
    return {
        "object": "list",
        "data": [
            {
                "id": spec.id,
                "object": "model",
                "created": 1735689600,
                "owned_by": spec.provider,
            }
            for spec in MODEL_SPECS
            if spec.provider in available
        ],
    }
