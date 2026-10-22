from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_MAX_DECLARED_BODY = 32 * 1024 * 1024
_JSON_METHODS = {"POST", "PUT", "PATCH"}


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope["headers"]:
        if key == name:
            return value.decode("latin-1")
    return ""


def _normalize(body: bytes) -> bytes:
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return body.decode("gb18030").encode("utf-8")
        except UnicodeDecodeError:
            return body
    return body


class JsonCharsetMiddleware:
    """Decode JSON bodies that arrive in the Windows ANSI code page.

    Git Bash passes non-ASCII command-line arguments to native curl in the
    active code page (GBK on Chinese Windows), so a body that looks correct in
    the terminal reaches the server as GBK bytes. UTF-8 remains the default;
    GB18030 is only tried when the body is not valid UTF-8.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return
        body = await self._read(receive)
        if body is None:
            return
        normalized = _normalize(body)
        if normalized is not body:
            scope = self._replace_body(scope, normalized)
        await self.app(scope, _replay(normalized, receive), send)

    def _applies(self, scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] not in _JSON_METHODS:
            return False
        if not _header(scope, b"content-type").lower().startswith("application/json"):
            return False
        declared = _header(scope, b"content-length")
        try:
            length = int(declared) if declared else None
        except ValueError:
            length = None
        return length is None or length <= _MAX_DECLARED_BODY

    @staticmethod
    async def _read(receive: Receive) -> bytes | None:
        chunks: list[bytes] = []
        while True:
            message: Message = await receive()
            if message["type"] == "http.disconnect":
                return None
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                return b"".join(chunks)

    @staticmethod
    def _replace_body(scope: Scope, body: bytes) -> Scope:
        headers = [(k, v) for k, v in scope["headers"] if k != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode("ascii")))
        replaced = dict(scope)
        replaced["headers"] = headers
        return replaced


def _replay(body: bytes, receive: Receive):
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay
