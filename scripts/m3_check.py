"""M3 acceptance: attachment upload through the OpenAI-compatible API."""

from __future__ import annotations

import base64
import struct
import sys
import zlib

import httpx

BASE = "http://127.0.0.1:8000/v1/chat/completions"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    if not ok:
        failures.append(name)


def make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Build a solid-colour PNG without external dependencies."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def post(content_parts, model: str = "deepseek-chat", session_id: str | None = None):
    body = {"model": model, "messages": [{"role": "user", "content": content_parts}]}
    if session_id:
        body["session_id"] = session_id
    r = httpx.post(BASE, json=body, timeout=600)
    if r.status_code != 200:
        return None, None, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    return (
        data["choices"][0]["message"]["content"],
        data.get("session_id") or r.headers.get("X-Session-Id"),
        None,
    )


def post_messages(messages, model: str = "deepseek-chat", session_id: str | None = None):
    body = {"model": model, "messages": messages}
    if session_id:
        body["session_id"] = session_id
    r = httpx.post(BASE, json=body, timeout=600)
    if r.status_code != 200:
        return None, None, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    return (
        data["choices"][0]["message"]["content"],
        data.get("session_id") or r.headers.get("X-Session-Id"),
        None,
    )


def data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def main() -> None:
    print("--- 1) image attachment ---")
    png = make_png(64, 64, (255, 0, 0))
    reply, sid, err = post(
        [
            {"type": "text", "text": "这张图是什么颜色？只回答颜色。"},
            {"type": "image_url", "image_url": {"url": data_url(png, "image/png")}},
        ]
    )
    check("image_reply", bool(reply and reply.strip()), f"err={err} reply={str(reply)[:80]!r}")
    check("image_color_detected", "红" in (reply or ""), f"reply={str(reply)[:80]!r}")

    print("\n--- 2) text file attachment ---")
    doc = "项目代号是蓝鸟计划，编号 8848。".encode()
    reply2, sid2, err2 = post(
        [
            {"type": "text", "text": "文件里的项目代号是什么？只回答代号。"},
            {
                "type": "file",
                "file": {
                    "filename": "plan.txt",
                    "file_data": base64.b64encode(doc).decode(),
                },
            },
        ]
    )
    check("doc_reply", bool(reply2 and reply2.strip()), f"err={err2} reply={str(reply2)[:80]!r}")
    check("doc_content_read", "蓝鸟" in (reply2 or ""), f"reply={str(reply2)[:80]!r}")

    print("\n--- 3) attachment in a continuing conversation ---")
    reply3, sid3, err3 = post_messages(
        [{"role": "user", "content": "记住我的幸运数字是 7。只回复OK。"}]
    )
    check("plain_turn", bool(reply3), f"err={err3}")
    reply4, sid4, err4 = post_messages(
        [
            {"role": "user", "content": "记住我的幸运数字是 7。只回复OK。"},
            {"role": "assistant", "content": reply3},
            {"role": "user", "content": "我的幸运数字是什么？只回答数字。"},
        ],
        session_id=sid3,
    )
    check("multi_turn_continues", bool(reply4 and "7" in reply4), f"err={err4} reply={str(reply4)[:60]!r}")

    print("\n--- 3b) attachment on a follow-up turn of the same session ---")
    reply4b, sid4b, err4b = post_messages(
        [
            {"role": "user", "content": "记住我的幸运数字是 7。只回复OK。"},
            {"role": "assistant", "content": reply3},
            {"role": "user", "content": "我的幸运数字是什么？只回答数字。"},
            {"role": "assistant", "content": reply4},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "图片里是什么颜色？只回答颜色。"},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url(make_png(48, 48, (0, 255, 0)), "image/png")},
                    },
                ],
            },
        ],
        session_id=sid3,
    )
    check(
        "follow_up_with_attachment",
        bool(reply4b and "绿" in reply4b),
        f"err={err4b} reply={str(reply4b)[:80]!r}",
    )
    check("follow_up_same_session", sid4b == sid3, f"{sid4b} vs {sid3}")

    print("\n--- 4) attachment + reasoning model ---")
    reply5, sid5, err5 = post(
        [
            {"type": "text", "text": "结合图片，说出图中唯一的颜色。只回答颜色。"},
            {"type": "image_url", "image_url": {"url": data_url(make_png(32, 32, (0, 0, 255)), "image/png")}},
        ],
        model="deepseek-reasoner",
    )
    check("reasoner_with_image", bool(reply5 and reply5.strip()), f"err={err5} reply={str(reply5)[:80]!r}")
    check("reasoner_image_color", "蓝" in (reply5 or ""), f"reply={str(reply5)[:80]!r}")

    print("\n--- 5) rejections ---")
    with httpx.Client(timeout=60) as c:
        r = c.post(
            BASE,
            json={
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "hi"},
                            {"type": "image_url", "image_url": {"url": "file:///etc/passwd"}},
                        ],
                    }
                ],
            },
        )
        check("bad_url_rejected", r.status_code == 400, f"{r.status_code} {r.text[:120]}")
        r2 = c.post(
            BASE,
            json={
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "hi"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!!"}},
                        ],
                    }
                ],
            },
        )
        check("bad_base64_rejected", r2.status_code == 400, f"{r2.status_code} {r2.text[:120]}")

    print("\n== result ==")
    if failures:
        print("FAILED:", failures)
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
