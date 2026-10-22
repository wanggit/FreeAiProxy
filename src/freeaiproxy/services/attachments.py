"""Attachment handling: turn OpenAI-style content parts into local files.

The gateway accepts the multimodal content shape used by OpenAI clients:

    {"role": "user", "content": [
        {"type": "text", "text": "what is in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        {"type": "file", "file": {"filename": "a.pdf", "file_data": "<base64>"}},
    ]}

Every attachment is materialised as a real file on disk (the browser can only
upload files) and handed to Playwright afterwards.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from freeaiproxy.config import AttachConfig
from freeaiproxy.engine.errors import InvalidRequestError

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]+)?(?P<b64>;base64)?,(?P<payload>.*)$", re.S)


@dataclass
class Attachment:
    """One file to upload, already written to disk."""

    path: Path
    filename: str
    media_type: str
    size: int
    origin: str  # "inline" | "remote" | "file"

    def cleanup(self) -> None:
        shutil.rmtree(self.path.parent, ignore_errors=True)


def _decode_base64(data: str) -> bytes:
    """Strict base64 decode.

    ``validate=True`` rejects characters outside the base64 alphabet, so a
    corrupted payload is reported instead of silently decoding to garbage.
    Padding errors are tolerated because some clients drop the '=' padding.
    """
    cleaned = "".join(data.split())
    padding = (-len(cleaned)) % 4
    try:
        return base64.b64decode(cleaned + "=" * padding, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidRequestError("attachment payload is not valid base64") from exc


def _safe_filename(name: str) -> str:
    name = Path(name).name.strip().replace("\x00", "")
    if not name:
        name = "attachment"
    return name[:120]


def _guess_media_type(filename: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or fallback


def _extension_allowed(filename: str, allowed: list[str]) -> bool:
    ext = Path(filename).suffix.lower()
    if not ext:
        return False
    return ext in {a.lower() for a in allowed}


class AttachmentStore:
    """Creates the temporary directory that holds uploads for one request."""

    def __init__(self, config: AttachConfig) -> None:
        self.config = config
        self._dir: Path | None = None

    def _ensure_dir(self) -> Path:
        if self._dir is None:
            self._dir = Path(tempfile.mkdtemp(prefix="freeaiproxy-attach-"))
        return self._dir

    def cleanup(self) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def _target_path(self, filename: str) -> Path:
        """Keep the original file name (the web UI shows it) but isolate it.

        Two uploads of the same name must not collide, so every file gets its
        own sub-directory while keeping the visible name intact.
        """
        directory = self._ensure_dir()
        digest = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:8]
        target_dir = directory / digest
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / filename

    def _validate(self, filename: str, size: int) -> None:
        limit = self.config.max_size_mb * 1024 * 1024
        if size > limit:
            raise InvalidRequestError(
                f"attachment '{filename}' is {size} bytes, over the {self.config.max_size_mb}MB limit"
            )

    def add_inline(self, filename: str, data_b64: str, media_type: str | None = None) -> Attachment:
        raw = _decode_base64(data_b64)
        name = _safe_filename(filename)
        self._validate(name, len(raw))
        path = self._target_path(name)
        path.write_bytes(raw)
        return Attachment(
            path=path,
            filename=name,
            media_type=media_type or _guess_media_type(name),
            size=len(raw),
            origin="inline",
        )

    def add_data_url(self, url: str) -> Attachment:
        match = _DATA_URL_RE.match(url.strip())
        if not match:
            raise InvalidRequestError("image_url is not a data URL")
        mime = match.group("mime") or "application/octet-stream"
        payload = match.group("payload")
        if match.group("b64"):
            raw = _decode_base64(payload)
        else:
            from urllib.parse import unquote_to_bytes

            raw = unquote_to_bytes(payload)
        ext = mimetypes.guess_extension(mime) or ""
        name = _safe_filename(f"image{ext}")
        self._validate(name, len(raw))
        path = self._target_path(name)
        path.write_bytes(raw)
        return Attachment(
            path=path, filename=name, media_type=mime, size=len(raw), origin="inline"
        )

    def add_remote(self, url: str) -> Attachment:
        if not self.config.download_remote_urls:
            raise InvalidRequestError(
                "remote attachment URLs are disabled; send the file inline as base64"
            )
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise InvalidRequestError("only http(s) attachment URLs can be downloaded")
        limit = self.config.remote_max_bytes
        try:
            with (
                httpx.Client(timeout=self.config.remote_timeout_s, follow_redirects=True) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise InvalidRequestError(
                            f"remote attachment exceeds {limit // (1024 * 1024)}MB"
                        )
                    chunks.append(chunk)
                raw = b"".join(chunks)
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
        except httpx.HTTPError as exc:
            raise InvalidRequestError(f"failed to download attachment: {exc}") from exc
        name = _safe_filename(Path(parsed.path).name or "attachment")
        self._validate(name, len(raw))
        path = self._target_path(name)
        path.write_bytes(raw)
        return Attachment(
            path=path,
            filename=name,
            media_type=content_type or _guess_media_type(name),
            size=len(raw),
            origin="remote",
        )
