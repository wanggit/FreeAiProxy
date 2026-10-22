from __future__ import annotations

import pytest

from freeaiproxy.engine.errors import InvalidRequestError
from freeaiproxy.services.models import (
    SUPPORTED_MODELS,
    get_model_spec,
    validate_model,
)

EXPECTED = {
    "deepseek-chat",
    "deepseek-reasoner",
    "yuanbao-hunyuan",
    "yuanbao-hunyuan-thinking",
    "yuanbao-hunyuan-instant",
    "yuanbao-hunyuan-search",
    "yuanbao-writing",
    "yuanbao-image",
    "yuanbao-ppt",
}


def test_registry_contains_all_models():
    assert set(SUPPORTED_MODELS) == EXPECTED


def test_yuanbao_hunyuan_maps_to_hy4_expert():
    spec = get_model_spec("yuanbao-hunyuan")
    assert spec.provider == "yuanbao"
    assert spec.ui_model == "Hy4 preview"
    assert spec.ui_mode == "Expert"


def test_yuanbao_thinking_and_search_flags():
    thinking = get_model_spec("yuanbao-hunyuan-thinking")
    assert thinking.ui_mode == "Thinking"
    assert thinking.search is False
    search = get_model_spec("yuanbao-hunyuan-search")
    assert search.ui_mode == "Instant"
    assert search.search is True


def test_image_and_artifact_models():
    assert get_model_spec("yuanbao-image").kind == "image"
    assert get_model_spec("yuanbao-writing").agent == "writing"
    assert get_model_spec("yuanbao-ppt").agent == "ppt"


def test_validate_model_returns_id():
    assert validate_model("yuanbao-ppt") == "yuanbao-ppt"


def test_unknown_model_rejected():
    with pytest.raises(InvalidRequestError):
        get_model_spec("gpt-4o")
