from __future__ import annotations

import base64

import pytest

from freeaiproxy.config import AttachConfig
from freeaiproxy.engine.errors import InvalidRequestError
from freeaiproxy.services.attachments import AttachmentStore

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAFElEQVR4nGP8z8DAwMDAxMDAw"
    "MAAAA4AAwDxAUsAAAAASUVORK5CYII="
)


def make_store(**overrides) -> AttachmentStore:
    config = AttachConfig(**overrides)
    return AttachmentStore(config)


def test_add_inline_writes_the_file():
    store = make_store()
    att = store.add_inline("notes.txt", base64.b64encode(b"hello").decode())
    try:
        assert att.path.exists()
        assert att.path.read_bytes() == b"hello"
        assert att.filename == "notes.txt"
        assert att.size == 5
        assert att.origin == "inline"
    finally:
        store.cleanup()


def test_add_data_url_image():
    store = make_store()
    b64 = base64.b64encode(PNG_1PX).decode()
    att = store.add_data_url(f"data:image/png;base64,{b64}")
    try:
        assert att.path.read_bytes() == PNG_1PX
        assert att.filename.endswith(".png")
        assert att.media_type == "image/png"
    finally:
        store.cleanup()


def test_size_limit_is_enforced():
    store = make_store(max_size_mb=1)
    payload = base64.b64encode(b"x" * (2 * 1024 * 1024)).decode()
    with pytest.raises(InvalidRequestError):
        store.add_inline("big.bin", payload)
    store.cleanup()


def test_invalid_base64_is_rejected():
    store = make_store()
    with pytest.raises(InvalidRequestError):
        store.add_inline("x.txt", "!!!not base64!!!")
    store.cleanup()


def test_remote_url_must_be_http():
    store = make_store()
    with pytest.raises(InvalidRequestError):
        store.add_remote("file:///etc/passwd")
    store.cleanup()


def test_filename_is_sanitised():
    store = make_store()
    att = store.add_inline("../../evil.txt", base64.b64encode(b"x").decode())
    try:
        assert "/" not in att.filename
        assert att.filename == "evil.txt"
    finally:
        store.cleanup()


def test_cleanup_removes_the_directory():
    store = make_store()
    att = store.add_inline("a.txt", base64.b64encode(b"x").decode())
    directory = att.path.parent
    store.cleanup()
    assert not directory.exists()
