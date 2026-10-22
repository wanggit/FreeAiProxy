"""M2 acceptance: deepseek-reasoner + FIFO queue serialisation."""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

BASE = "http://127.0.0.1:8000"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    if not ok:
        failures.append(name)


def chat(model: str, content: str, session_id: str | None = None, stream: bool = False):
    body = {"model": model, "messages": [{"role": "user", "content": content}], "stream": stream}
    if session_id:
        body["session_id"] = session_id
    with httpx.Client(timeout=600) as c:
        if stream:
            with c.stream("POST", f"{BASE}/v1/chat/completions", json=body) as r:
                assert r.status_code == 200, r.read()
                sid = r.headers.get("X-Session-Id")
                text = []
                for line in r.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    obj = json.loads(payload)
                    for ch in obj.get("choices", []):
                        text.append(ch.get("delta", {}).get("content") or "")
                return "".join(text), sid
        r = c.post(f"{BASE}/v1/chat/completions", json=body)
        if r.status_code != 200:
            return None, r.status_code
        data = r.json()
        return data["choices"][0]["message"]["content"], data.get("session_id")


def main() -> None:
    with httpx.Client(timeout=30) as c:
        models = c.get(f"{BASE}/v1/models").json()
        ids = [m["id"] for m in models["data"]]
        check("models_include_reasoner", "deepseek-reasoner" in ids, str(ids))

    print("\n--- reasoner: math question ---")
    t0 = time.time()
    reply, _ = chat("deepseek-reasoner", "9.11 和 9.9 哪个大？直接给结论。")
    elapsed = time.time() - t0
    check("reasoner_reply", bool(reply and reply.strip()), f"{elapsed:.1f}s reply={str(reply)[:80]!r}")
    check("reasoner_conclusion", "9.9" in (reply or "") or "9.11" in (reply or ""), str(reply)[:80])
    check("reasoner_no_think_leak", "我们需要回答" not in (reply or ""), "reasoning trace not exposed")

    print("\n--- normal model still works after reasoner ---")
    reply2, _ = chat("deepseek-chat", "用一个字形容天空。")
    check("chat_model_after_reasoner", bool(reply2 and reply2.strip()), f"reply={str(reply2)[:60]!r}")

    print("\n--- queue: 4 concurrent requests are serialised ---")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(chat, "deepseek-chat", f"请说出第{i}个英文字母，只输出字母。")
            for i in range(4)
        ]
        results = [f.result() for f in futures]
    total = time.time() - t0
    ok = all(r[0] and r[0].strip() for r in results)
    check("concurrent_all_replied", ok, f"total={total:.1f}s replies={[str(r[0])[:12] for r in results]}")
    check("queue_serialised", total > 4.0, f"4 requests took {total:.1f}s (serialised, not parallel)")
    check("queue_distinct_sessions", len({r[1] for r in results}) == 4, str([r[1] for r in results]))

    print("\n--- admin reports queue state ---")
    with httpx.Client(timeout=30) as c:
        st = c.get(f"{BASE}/admin/status").json()
        check("admin_queue_stats", "queue" in st and st["queue"]["processed"] > 0, json.dumps(st.get("queue")))

    print("\n== result ==")
    if failures:
        print("FAILED:", failures)
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
