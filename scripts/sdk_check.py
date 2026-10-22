"""Verify the gateway works with the official openai-python SDK."""

from __future__ import annotations

import sys

from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="not-needed")

print("--- models ---")
models = client.models.list()
print("models:", [m.id for m in models.data])

print("\n--- non-streaming ---")
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "用一个字形容今天的天气，不要解释。"}],
)
print("reply:", repr(resp.choices[0].message.content))
print("finish_reason:", resp.choices[0].finish_reason)
print("session_id(header):", resp._request.headers.get("X-Session-Id") if hasattr(resp, "_request") else "n/a")

sid = resp.model_extra.get("session_id") if resp.model_extra else None
print("session_id(body):", sid)

print("\n--- streaming ---")
stream = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "从1数到5，只输出数字。"}],
    stream=True,
)
chunks = []
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        chunks.append(chunk.choices[0].delta.content)
print("streamed:", repr("".join(chunks)))

print("\n--- multi-turn with extra_headers ---")
r1 = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "记住数字42，只回复OK。"}],
)
sid1 = r1.model_extra.get("session_id")
r2 = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "user", "content": "记住数字42，只回复OK。"},
        {"role": "assistant", "content": r1.choices[0].message.content},
        {"role": "user", "content": "我让你记的数字是多少？只回复数字。"},
    ],
    extra_headers={"X-Session-Id": sid1},
)
print("turn1:", repr(r1.choices[0].message.content), "sid:", sid1)
print("turn2:", repr(r2.choices[0].message.content))

print("\n--- extra_body session_id ---")
r3 = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "user", "content": "记住数字42，只回复OK。"},
        {"role": "assistant", "content": r1.choices[0].message.content},
        {"role": "user", "content": "再说一遍那个数字，只回复数字。"},
    ],
    extra_body={"session_id": sid1},
)
print("turn3:", repr(r3.choices[0].message.content))

ok = "42" in (r2.choices[0].message.content or "") and "42" in (r3.choices[0].message.content or "")
print("\nRESULT:", "SDK COMPAT OK" if ok else "SDK COMPAT FAILED")
sys.exit(0 if ok else 1)
