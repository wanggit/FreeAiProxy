from __future__ import annotations

import pytest

from freeaiproxy.api.v1_images import _normalize_ratio
from freeaiproxy.engine import yuanbao_selectors as YB
from freeaiproxy.engine.errors import InvalidRequestError


@pytest.mark.parametrize("size", [None, "", "auto", "smart", "default", "AUTO"])
def test_auto_sizes_map_to_none(size: str | None) -> None:
    assert _normalize_ratio(size) is None


@pytest.mark.parametrize("ratio", YB.IMAGE_RATIOS)
def test_supported_ratios_pass_through(ratio: str) -> None:
    assert _normalize_ratio(ratio) == ratio


def test_ratio_is_case_and_space_tolerant() -> None:
    assert _normalize_ratio(" 16:9 ") == "16:9"


@pytest.mark.parametrize("size", ["1024x1024", "21:9", "square"])
def test_unsupported_size_rejected(size: str) -> None:
    with pytest.raises(InvalidRequestError):
        _normalize_ratio(size)
