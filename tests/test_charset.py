from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from freeaiproxy.api.middleware import JsonCharsetMiddleware


def _make_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(JsonCharsetMiddleware)

    @app.post("/echo")
    async def echo(request: Request):
        return await request.json()

    @app.post("/raw")
    async def raw(request: Request):
        return {"hex": (await request.body()).hex()}

    return TestClient(app)


def _post(path: str, body: bytes, content_type: str = "application/json"):
    return _make_client().post(
        path, content=body, headers={"Content-Type": content_type}
    )


def test_gbk_body_is_transcoded() -> None:
    response = _post("/echo", '{"text":"你好"}'.encode("gbk"))
    assert response.status_code == 200
    assert response.json() == {"text": "你好"}


def test_utf8_body_passes_through() -> None:
    response = _post("/echo", '{"text":"你好"}'.encode())
    assert response.status_code == 200
    assert response.json() == {"text": "你好"}


def test_ascii_body_is_untouched() -> None:
    response = _post("/echo", b'{"text":"hello"}')
    assert response.status_code == 200
    assert response.json() == {"text": "hello"}


def test_non_json_content_type_is_ignored() -> None:
    body = '{"text":"你好"}'.encode("gbk")
    response = _post("/raw", body, content_type="text/plain")
    assert response.status_code == 200
    assert response.json()["hex"] == body.hex()
