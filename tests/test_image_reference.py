from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from freeaiproxy.api.v1_images import (
    _check_reference_support,
    _collect_ref_urls,
    _ImageRequest,
    _normalize_style,
    _refs_from_uploads,
    _refs_from_urls,
)
from freeaiproxy.config import AttachConfig, Settings
from freeaiproxy.engine import doubao_selectors as DB
from freeaiproxy.engine.errors import InvalidRequestError
from freeaiproxy.services.attachments import AttachmentStore
from freeaiproxy.services.models import MODELS, get_model_spec

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAFElEQVR4nGP8z8DAwMDAxMDAw"
    "MAAAA4AAwDxAUsAAAAASUVORK5CYII="
)
PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()


def make_request(settings: Settings | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings or Settings()))
    )


# ------------------------------------------------------------------ capability


def test_image_models_declare_reference_support() -> None:
    assert MODELS["yuanbao-image"].ref_image is True
    assert MODELS["doubao-image"].ref_image is True
    assert MODELS["chatglm-image"].ref_image is True
    # The wenxin web image composer has no upload entry (calibrated 2026-10-24).
    assert MODELS["wenxin-image"].ref_image is False


def test_unsupported_model_rejects_reference_images() -> None:
    spec = get_model_spec("wenxin-image")
    with pytest.raises(InvalidRequestError, match="does not accept"):
        _check_reference_support(make_request(), spec, 1)


def test_disabled_attachments_reject_reference_images() -> None:
    settings = Settings()
    settings.attach.enabled = False
    spec = get_model_spec("yuanbao-image")
    with pytest.raises(InvalidRequestError, match="disabled"):
        _check_reference_support(make_request(settings), spec, 1)


def test_reference_image_count_is_capped() -> None:
    spec = get_model_spec("yuanbao-image")
    _check_reference_support(make_request(), spec, 1)
    with pytest.raises(InvalidRequestError, match="at most"):
        _check_reference_support(make_request(), spec, 2)


# ------------------------------------------------------------------ url shapes


def test_collect_ref_urls_accepts_str_and_lists() -> None:
    assert _collect_ref_urls(_ImageRequest(prompt="p", image=PNG_DATA_URL)) == [PNG_DATA_URL]
    req = _ImageRequest(prompt="p", image=[PNG_DATA_URL], images=[PNG_DATA_URL])
    assert len(_collect_ref_urls(req)) == 2
    assert _collect_ref_urls(_ImageRequest(prompt="p")) == []


def test_refs_from_urls_materialises_data_url() -> None:
    store = AttachmentStore(AttachConfig())
    try:
        refs = _refs_from_urls(store, [PNG_DATA_URL])
        assert len(refs) == 1
        assert refs[0].path.read_bytes() == PNG_1PX
        assert refs[0].media_type == "image/png"
    finally:
        store.cleanup()


def test_refs_from_urls_rejects_non_image_and_garbage() -> None:
    store = AttachmentStore(AttachConfig())
    try:
        text_url = "data:text/plain;base64," + base64.b64encode(b"hi").decode()
        with pytest.raises(InvalidRequestError, match="not an image"):
            _refs_from_urls(store, [text_url])
        with pytest.raises(InvalidRequestError, match="data URL or an http"):
            _refs_from_urls(store, ["ftp://example.com/a.png"])
        with pytest.raises(InvalidRequestError, match="empty"):
            _refs_from_urls(store, ["   "])
    finally:
        store.cleanup()


# ------------------------------------------------------------------ multipart


class FakeUpload:
    """Minimal stand-in for starlette UploadFile (only the fields we read)."""

    def __init__(self, filename: str, raw: bytes, content_type: str) -> None:
        self.filename = filename
        self.content_type = content_type
        self._raw = raw

    async def read(self) -> bytes:
        return self._raw


def make_upload(name: str, raw: bytes, content_type: str) -> FakeUpload:
    return FakeUpload(name, raw, content_type)


async def test_refs_from_uploads_accepts_png() -> None:
    store = AttachmentStore(AttachConfig())
    try:
        refs = await _refs_from_uploads(store, [make_upload("ref.png", PNG_1PX, "image/png")])
        assert refs[0].path.read_bytes() == PNG_1PX
        assert refs[0].origin == "file"
    finally:
        store.cleanup()


async def test_refs_from_uploads_rejects_empty_and_non_image() -> None:
    store = AttachmentStore(AttachConfig())
    try:
        with pytest.raises(InvalidRequestError, match="empty"):
            await _refs_from_uploads(store, [make_upload("a.png", b"", "image/png")])
        with pytest.raises(InvalidRequestError, match="not an image"):
            await _refs_from_uploads(store, [make_upload("a.txt", b"hi", "text/plain")])
        with pytest.raises(InvalidRequestError, match="must be one of"):
            await _refs_from_uploads(store, [make_upload("a.tiff", PNG_1PX, "image/tiff")])
    finally:
        store.cleanup()


# ------------------------------------------------------------------ style gate


def test_style_normalisation_only_applies_to_doubao() -> None:
    doubao = get_model_spec("doubao-image")
    assert _normalize_style(DB.IMAGE_STYLES[0], doubao) == DB.IMAGE_STYLES[0]
    with pytest.raises(InvalidRequestError):
        _normalize_style("no-such-style", doubao)
    yuanbao = get_model_spec("yuanbao-image")
    assert _normalize_style("anything", yuanbao) == "anything"
    assert _normalize_style(None, doubao) is None
