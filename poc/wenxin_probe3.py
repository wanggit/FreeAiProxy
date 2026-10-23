"""Wenxin chat send + completion probe (calibration aid).

Logged-in profile assumed. Sends a plain chat message, then samples the DOM so
we can see the assistant message container, the "generating/stop" signal, and
when the reply stabilises. Writes JSON + screenshots into poc/dump/wenxin/.

    uv run python poc/wenxin_probe3.py
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

DUMP_DIR = ROOT / "poc" / "dump" / "wenxin"
PROFILE_DIR = ROOT / "data" / "profiles" / "wenxin"
START_URL = "https://wenxin.baidu.com/"
PROMPT = "用一句话介绍杭州"

INV_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const txt = (el) => (el.innerText || '').replace(/\\s+/g, ' ').trim();
  const byClass = {};
  for (const el of document.querySelectorAll('[class]')) {
    for (const c of String(el.className).split(/\\s+/)) {
      if (!/message|msg|chat|answer|question|reply|markdown|content|bubble|stop|loading|generat|send|submit|capsule|ci-panel/i.test(c)) continue;
      byClass[c] = (byClass[c] || 0) + 1;
    }
  }
  const stops = Array.from(document.querySelectorAll('button,[role="button"],span,div,img'))
    .filter(el => visible(el))
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      text: (txt(el) || el.getAttribute('aria-label') || '').slice(0, 30),
      cls: String(el.className || '').slice(0, 120),
      id: el.id || '',
    }))
    .filter(o => /停止|stop|生成中|loading|generat/i.test(o.text + ' ' + o.cls + ' ' + o.id))
    .slice(0, 20);
  const md = Array.from(document.querySelectorAll('[class*="markdown"],[class*="answer"],[class*="content"]'))
    .filter(el => visible(el) && txt(el).length > 0)
    .map(el => ({cls: String(el.className||'').slice(0,140), len: txt(el).length, sample: txt(el).slice(0,120)}))
    .slice(0, 25);
  return {
    url: location.href,
    classCounts: Object.fromEntries(Object.entries(byClass).sort((a,b)=>b[1]-a[1]).slice(0,60)),
    stops,
    md,
    submitPresent: !!document.querySelector('.ci-submit-button'),
    capsuleTexts: Array.from(document.querySelectorAll('.capsule-text')).map(el=>txt(el)).slice(0,20),
  };
}
"""


def _save(name: str, data) -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def main() -> None:
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
        locale="zh-CN",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.locator("textarea.ci-textarea").first.wait_for(state="visible", timeout=30000)
        await asyncio.sleep(1.5)
        _save("s_before", await page.evaluate(INV_JS))

        box = page.locator("textarea.ci-textarea").first
        await box.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.8)
        submit = page.locator(".ci-submit-button").first
        if await submit.count() and await submit.is_visible():
            await submit.click()
            print("[probe] clicked submit", flush=True)
        else:
            await page.keyboard.press("Enter")
            print("[probe] pressed Enter", flush=True)

        t0 = time.monotonic()
        sample = 0
        last_len = -1
        while time.monotonic() - t0 < 90:
            await asyncio.sleep(3)
            sample += 1
            info = await page.evaluate(INV_JS)
            _save(f"s_sample_{sample:02d}", info)
            maxlen = max((m["len"] for m in info["md"]), default=0)
            print(
                f"[probe] t={int(time.monotonic()-t0)}s md_max={maxlen} "
                f"stops={len(info['stops'])} submit={info['submitPresent']}",
                flush=True,
            )
            if sample >= 3 and maxlen == last_len and maxlen > 0:
                await page.screenshot(path=str(DUMP_DIR / "s_final.png"))
                print("[probe] reply stable", flush=True)
                break
            last_len = maxlen
        await page.screenshot(path=str(DUMP_DIR / "s_end.png"))
        _save("s_end", await page.evaluate(INV_JS))


if __name__ == "__main__":
    asyncio.run(main())
