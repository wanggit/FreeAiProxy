"""Multi-turn check: reasoner mode, then switching back to the normal model."""

from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8000/v1/chat/completions"

Q1 = "记住：我最喜欢的颜色是蓝色。只回复OK。"
Q2 = "我最喜欢的颜色是什么？只回复颜色。"
Q3 = "再说一次我最喜欢的颜色？只回复颜色。"


def post(model: str, messages: list[dict], session_id: str | None = None):
    body = {"model": model, "messages": messages}
    if session_id:
        body["session_id"] = session_id
    r = httpx.post(BASE, json=body, timeout=600)
    if r.status_code != 200:
        return None, None, f"{r.status_code} {r.text[:200]}"
    data = r.json()
    return (
        data["choices"][0]["message"]["content"],
        data.get("session_id") or r.headers.get("X-Session-Id"),
        None,
    )


def main() -> None:
    a1, sid, err = post("deepseek-reasoner", [{"role": "user", "content": Q1}])
    print(f"turn1 reasoner: {a1!r} sid={sid} err={err}")
    assert a1 and sid, err

    a2, sid2, err = post(
        "deepseek-reasoner",
        [
            {"role": "user", "content": Q1},
            {"role": "assistant", "content": a1},
            {"role": "user", "content": Q2},
        ],
        sid,
    )
    print(f"turn2 reasoner: {a2!r} err={err}")
    assert a2 and "蓝" in a2, err
    assert sid2 == sid, (sid2, sid)

    a3, sid3, err = post(
        "deepseek-chat",
        [
            {"role": "user", "content": Q1},
            {"role": "assistant", "content": a1},
            {"role": "user", "content": Q2},
            {"role": "assistant", "content": a2},
            {"role": "user", "content": Q3},
        ],
        sid,
    )
    print(f"turn3 chat mode: {a3!r} err={err}")
    assert a3 and "蓝" in a3, err
    assert sid3 == sid, (sid3, sid)

    print("\nRESULT: MULTI-TURN MODEL SWITCHING OK")


if __name__ == "__main__":
    main()
