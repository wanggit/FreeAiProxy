"""End-to-end acceptance: multi-turn conversation against a running gateway."""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"
KEY = ""

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    if not ok:
        failures.append(name)


def headers() -> dict:
    return {"Authorization": f"Bearer {KEY}"} if KEY else {}


def chat(messages: list[dict], session_id: str | None = None, stream: bool = False):
    body = {"model": "deepseek-chat", "messages": messages, "stream": stream}
    if session_id:
        body["session_id"] = session_id
    if stream:
        with (
            httpx.Client(timeout=300) as c,
            c.stream("POST", f"{BASE}/v1/chat/completions", json=body, headers=headers()) as r,
        ):
            assert r.status_code == 200, r.read()
            sid_header = r.headers.get("X-Session-Id")
            text = []
            sid_chunk = None
            done = False
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    done = True
                    break
                obj = json.loads(payload)
                sid_chunk = obj.get("session_id") or sid_chunk
                for ch in obj.get("choices", []):
                    text.append(ch.get("delta", {}).get("content") or "")
            return "".join(text), sid_header, sid_chunk, done
    with httpx.Client(timeout=300) as c:
        r = c.post(f"{BASE}/v1/chat/completions", json=body, headers=headers())
        if r.status_code != 200:
            return None, r.headers.get("X-Session-Id"), None, r.text
        data = r.json()
        return (
            data["choices"][0]["message"]["content"],
            r.headers.get("X-Session-Id"),
            data.get("session_id"),
            None,
        )


def main() -> None:
    with httpx.Client(timeout=30) as c:
        health = c.get(f"{BASE}/healthz")
        check("healthz", health.status_code == 200, json.dumps(health.json(), ensure_ascii=False))
        models = c.get(f"{BASE}/v1/models", headers=headers())
        check("models", models.status_code == 200, models.text[:120])

    print("\n--- turn 1: new conversation (no session id) ---")
    msgs = [{"role": "user", "content": "我叫小明，请记住我的名字。只回复“好的”。"}]
    r1, sid1, sid1_body, _ = chat(msgs)
    check("turn1_reply", bool(r1 and r1.strip()), f"reply={r1!r}")
    check("turn1_session_header", bool(sid1), f"sid={sid1}")
    check("turn1_session_body", sid1 == sid1_body, f"header={sid1} body={sid1_body}")

    print("\n--- turn 2: same session id, second message ---")
    msgs2 = msgs + [{"role": "assistant", "content": r1}, {"role": "user", "content": "我叫什么名字？只回复名字。"}]
    r2, sid2, _, _ = chat(msgs2, session_id=sid1)
    check("turn2_reply", bool(r2 and r2.strip()), f"reply={r2!r}")
    check("turn2_same_session", sid2 == sid1, f"{sid2} vs {sid1}")
    check("turn2_remembers", "小明" in (r2 or ""), f"reply={r2!r}")

    print("\n--- turn 3: auto-match without session id (client resends full history) ---")
    msgs3 = msgs2 + [{"role": "assistant", "content": r2}, {"role": "user", "content": "再确认一次我的名字，只回复名字。"}]
    r3, sid3, _, _ = chat(msgs3)
    check("turn3_reply", bool(r3 and r3.strip()), f"reply={r3!r}")
    check("turn3_same_session", sid3 == sid1, f"{sid3} vs {sid1}")
    check("turn3_remembers", "小明" in (r3 or ""), f"reply={r3!r}")

    print("\n--- turn 4: streaming ---")
    msgs4 = msgs3 + [{"role": "assistant", "content": r3}, {"role": "user", "content": "数到5，只输出数字。"}]
    r4, sid4, sid4c, done = chat(msgs4, session_id=sid1, stream=True)
    check("turn4_stream_reply", bool(r4 and r4.strip()), f"reply={r4!r}")
    check("turn4_stream_sid", sid4 == sid1 and sid4c == sid1, f"header={sid4} chunk={sid4c}")
    check("turn4_done_marker", bool(done), "data: [DONE] received")
    check("turn4_same_session", sid4 == sid1)

    print("\n--- turn 5: fresh conversation without id (should NOT reuse session) ---")
    r5, sid5, _, _ = chat([{"role": "user", "content": "刚才我叫什么名字？只回复“不知道”或名字。"}])
    check("turn5_new_session", sid5 != sid1, f"{sid5} vs {sid1}")

    print("\n--- error handling ---")
    with httpx.Client(timeout=30) as c:
        bad_model = c.post(
            f"{BASE}/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers=headers(),
        )
        check("bad_model_400", bad_model.status_code == 400, bad_model.text[:160])
        check("bad_model_openai_shape", "error" in bad_model.json(), bad_model.text[:160])
        bad_session = c.post(
            f"{BASE}/v1/chat/completions",
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "conv_doesnotexist",
            },
            headers=headers(),
        )
        check("bad_session_404", bad_session.status_code == 404, bad_session.text[:160])
        bad_body = c.post(
            f"{BASE}/v1/chat/completions", json={"messages": []}, headers=headers()
        )
        check("bad_body_400", bad_body.status_code == 400, bad_body.text[:160])

    print("\n--- admin ---")
    with httpx.Client(timeout=30) as c:
        st = c.get(f"{BASE}/admin/status", headers=headers())
        check("admin_status", st.status_code == 200, st.text[:220])

    print("\n== result ==")
    if failures:
        print("FAILED:", failures)
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
