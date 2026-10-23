from __future__ import annotations

import pytest

from freeaiproxy.api.files import _NAME_RE
from freeaiproxy.api.v1_music import _normalize_choice
from freeaiproxy.engine import wenxin_selectors as WX
from freeaiproxy.engine.errors import InvalidRequestError


def test_normalize_style_choice():
    assert _normalize_choice(None, WX.MUSIC_STYLES, "style") is None
    assert _normalize_choice("  ", WX.MUSIC_STYLES, "style") is None
    assert _normalize_choice("摇滚", WX.MUSIC_STYLES, "style") == "摇滚"
    assert _normalize_choice("r&b", WX.MUSIC_STYLES, "style") == "R&B"
    with pytest.raises(InvalidRequestError):
        _normalize_choice("重金属核", WX.MUSIC_STYLES, "style")


def test_normalize_voice_choice():
    assert _normalize_choice("甜美女声", WX.MUSIC_VOICES, "voice") == "甜美女声"
    with pytest.raises(InvalidRequestError):
        _normalize_choice("海豚音", WX.MUSIC_VOICES, "voice")


def test_files_route_accepts_audio_names():
    assert _NAME_RE.match("0123456789abcdef0123456789abcdef.mp3")
    assert _NAME_RE.match("0123456789abcdef0123456789abcdef.wav")
    assert _NAME_RE.match("0123456789abcdef0123456789abcdef.flac")
    assert not _NAME_RE.match("0123456789abcdef0123456789abcdef.exe")
