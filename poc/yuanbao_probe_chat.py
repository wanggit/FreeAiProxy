"""Probe Yuanbao text chat: send, stream, completion, URL, reload.

Sends one harmless prompt in a new conversation and records DOM samples.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
START_URL = "https://yuanbao.tencent.com/"
PROMPT = "请用大约150字介绍杭州的秋天，不要标题，不要列表。"

SAMPLE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const keywords = ['markdown', 'assistant', 'message', 'answer', 'robot',
                    'think', 'search', 'user-content', 'reply'];
  const candidates = [];
  for (const el of document.querySelectorAll('body *')) {
    const cls = String(el.className || '');
    if (!cls || !keywords.some(k => cls.toLowerCase().includes(k))) continue;
    if (!visible(el)) continue;
    const text = (el.innerText || '').trim();
    if (!text) continue;
    candidates.push({
      tag: el.tagName.toLowerCase(),
      cls: cls.slice(0, 160),
      len: text.length,
      text: text.slice(0, 160),
    });
    if (candidates.length >= 120) break;
  }
  const aria = Array.from(document.querySelectorAll('[aria-label]'))
    .filter(visible)
    .map(el => el.getAttribute('aria-label'))
    .filter(x => x && /send|stop|pause|copy|regenerate|like|dislike|share/i.test(x));
  return {
    url: location.href,
    title: document.title,
    bodyLen: (document.body.innerText || '').length,
    aria: [...new Set(aria)],
    candidates,
  };
}
"""


async def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.locator(".ql-editor").first.wait_for(state="visible", timeout=30000)
        await asyncio.sleep(1.0)
        for label in ("Got It",):
            loc = page.get_by_role("button", name=label, exact=True)
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click()
                await asyncio.sleep(0.3)

        editor = page.locator(".ql-editor").first
        await editor.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.5)
        await page.screenshot(path=str(DUMP_DIR / "30_before_send.png"))

        send = page.locator("[aria-label='Send']").first
        if await send.count():
            await send.click()
        else:
            await page.keyboard.press("Enter")
        t0 = time.monotonic()
        print("[chat] sent", flush=True)

        samples = []
        last_len = -1
        stable = 0
        while time.monotonic() - t0 < 90:
            await asyncio.sleep(0.5)
            s = await page.evaluate(SAMPLE_JS)
            s["t"] = round(time.monotonic() - t0, 2)
            samples.append(s)
            max_len = max((c["len"] for c in s["candidates"]), default=0)
            print(f"[chat] t={s['t']:5.1f} body={s['bodyLen']:6} "
                  f"cand={len(s['candidates']):3} max={max_len:5} aria={s['aria']} url={s['url']}",
                  flush=True)
            if max_len == last_len:
                stable += 1
            else:
                stable = 0
                last_len = max_len
            if samples[-1]["t"] > 10 and max_len > 30 and stable >= 8:
                break

        final = await page.evaluate(SAMPLE_JS)
        (DUMP_DIR / "30_chat_samples.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "30_chat_final.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "30_chat_final.html").write_text(
            await page.content(), encoding="utf-8"
        )
        await page.screenshot(path=str(DUMP_DIR / "30_chat_final.png"))

        url = page.url
        print(f"[chat] final url={url}", flush=True)
        await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(4)
        reload_info = await page.evaluate(SAMPLE_JS)
        (DUMP_DIR / "31_chat_reload.json").write_text(
            json.dumps(reload_info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "31_chat_reload.html").write_text(
            await page.content(), encoding="utf-8"
        )
        await page.screenshot(path=str(DUMP_DIR / "31_chat_reload.png"))
        print(f"[chat] reload bodyLen={reload_info['bodyLen']} "
              f"candidates={len(reload_info['candidates'])}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
