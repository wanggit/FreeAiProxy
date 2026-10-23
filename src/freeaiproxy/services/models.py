from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from freeaiproxy.engine.errors import InvalidRequestError

Kind = Literal["chat", "image"]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    kind: Kind = "chat"
    mode: str = "chat"
    think: bool = False
    ui_model: str | None = None
    ui_mode: str | None = None
    search: bool = False
    agent: str | None = None


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("deepseek-chat", "deepseek"),
    ModelSpec("deepseek-reasoner", "deepseek", think=True),
    ModelSpec(
        "yuanbao-hunyuan",
        "yuanbao",
        mode="expert",
        ui_model="Hy4 preview",
        ui_mode="Expert",
    ),
    ModelSpec(
        "yuanbao-hunyuan-thinking",
        "yuanbao",
        mode="thinking",
        ui_model="Hy3",
        ui_mode="Thinking",
    ),
    ModelSpec(
        "yuanbao-hunyuan-instant",
        "yuanbao",
        mode="instant",
        ui_model="Hy3",
        ui_mode="Instant",
    ),
    ModelSpec(
        "yuanbao-hunyuan-search",
        "yuanbao",
        mode="search",
        ui_model="Hy3",
        ui_mode="Instant",
        search=True,
    ),
    ModelSpec("yuanbao-writing", "yuanbao", mode="writing", agent="writing"),
    ModelSpec(
        "yuanbao-image",
        "yuanbao",
        kind="image",
        mode="image",
        agent="image",
    ),
    ModelSpec("yuanbao-ppt", "yuanbao", mode="ppt", agent="ppt"),
    ModelSpec("wenxin-image", "wenxin", kind="image", mode="image", agent="image"),
    ModelSpec("wenxin-writing", "wenxin", mode="writing", agent="writing"),
)

MODELS: dict[str, ModelSpec] = {spec.id: spec for spec in MODEL_SPECS}
SUPPORTED_MODELS: tuple[str, ...] = tuple(MODELS)


def get_model_spec(model: str) -> ModelSpec:
    spec = MODELS.get(model)
    if spec is None:
        raise InvalidRequestError(
            f"model '{model}' is not supported; supported: {sorted(SUPPORTED_MODELS)}"
        )
    return spec


def validate_model(model: str) -> str:
    return get_model_spec(model).id
