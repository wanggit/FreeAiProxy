from __future__ import annotations

import pytest

from freeaiproxy.api.v1_videos import _normalize_duration, _normalize_ratio
from freeaiproxy.engine import doubao_selectors as DB
from freeaiproxy.engine.errors import InvalidRequestError


@pytest.mark.parametrize("size", [None, "", "auto", "smart", "default"])
def test_auto_sizes_map_to_none(size: str | None) -> None:
    assert _normalize_ratio(size, "doubao") is None


@pytest.mark.parametrize("ratio", DB.VIDEO_RATIOS)
def test_supported_ratios_pass_through(ratio: str) -> None:
    assert _normalize_ratio(ratio, "doubao") == ratio


def test_ratio_is_case_and_space_tolerant() -> None:
    assert _normalize_ratio(" 21:9 ", "doubao") == "21:9"


@pytest.mark.parametrize("size", ["2:3", "1024x1024"])
def test_unsupported_ratio_rejected(size: str) -> None:
    with pytest.raises(InvalidRequestError):
        _normalize_ratio(size, "doubao")


@pytest.mark.parametrize("duration", [None, 4, 10, 15])
def test_supported_durations_pass_through(duration: int | None) -> None:
    assert _normalize_duration(duration) == duration


@pytest.mark.parametrize("duration", [3, 16, -1])
def test_unsupported_duration_rejected(duration: int) -> None:
    with pytest.raises(InvalidRequestError):
        _normalize_duration(duration)
