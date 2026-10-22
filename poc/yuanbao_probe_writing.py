"""Probe Yuanbao Writing (professional writing) mode and output DOM."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
START_URL = "https://yuanbao.tencent.com/"
PROMPT = "写一份关于城市夜跑活动的策划文案，面向25-35岁上班族，约400字，包含活动亮点和报名方式。"

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const markdowns = Array.from(document.querySelectorAll('.hyc-common-markdown')).filter(visible)
    .map(el => ({
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 500),
      done: !!el.closest('.hyc-content-md-done'),
    }));
  const editables = Array.from(document.querySelectorAll(
      '[contenteditable="true"],[contenteditable=""],textarea')).filter(visible)
    .map(el => ({
      cls: String(el.className || '').slice(0, 160),
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 300),
    }));
  const buttons = Array.from(document.querySelectorAll('button,[role="button"],a'))
    .filter(visible)
    .map(el => ({
      text: (el.innerText || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
      aria: el.getAttribute('aria-label') || '',
    }))
    .filter(x => /export|download|save|copy|share|publish|edit|preview|document|doc|send|stop/i.test(x.text + ' ' + x.aria))
    .slice(0, 80);
  const ai = Array.from(document.querySelectorAll('.agent-chat__list__item--ai')).map(el => ({
    outputting: el.getAttribute('data-conv-outputting'),
    textLen: (el.innerText || '').trim().length,
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 500),
  }));
  const docish = Array.from(document.querySelectorAll(
      '[class*="doc"],[class*="Doc"],[class*="write"],[class*="Write"],[class*="article"],[class*="Article"]'))
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 180),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
    }))
    .filter(x => x.text).slice(0, 80);
  return {
    url: location.href,
    bodyLen: (document.body.innerText || '').length,
    markdowns,
    editables,
    buttons,
    ai,
    docish,
    doneCount: document.querySelectorAll('.hyc-content-md-done').length,
    sendVisible: !!Array.from(document.querySelectorAll('[aria-label="Send"]')).find(visible),
    stopVisible: !!Array.from(document.querySelectorAll('[aria-label="Stop Answering"]')).find(visible),
  };
}
"""


async def js_click(page, selector: str) -> bool:
    return bool(await page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            el.click();
            return true;
        }""",
        selector,
    ))


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
        await page.locator("button[aria-label='Writing']").first.wait_for(
            state="visible", timeout=30000
        )
        await asyncio.sleep(1.5)

        await js_click(page, "button[aria-label='Writing']")
        await asyncio.sleep(2)
        await page.screenshot(path=str(DUMP_DIR / "80_writing_mode.png"))

        await page.locator(".ql-editor").first.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.5)
        await page.locator("[aria-label='Send']").first.click()
        print("[writing] sent", flush=True)

        t0 = time.monotonic()
        samples = []
        last_sig = ""
        stable = 0
        while time.monotonic() - t0 < 180:
            await asyncio.sleep(1.0)
            s = await page.evaluate(STATE_JS)
            s["t"] = round(time.monotonic() - t0, 1)
            samples.append(s)
            sig = "|".join(m["text"] for m in s["markdowns"]) + "|".join(
                e["text"] for e in s["editables"]
            )
            if sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            print(f"[writing] t={s['t']:5.1f} body={s['bodyLen']:5} "
                  f"md={[(m['len'], m['done']) for m in s['markdowns']]} "
                  f"edit={[(e['len']) for e in s['editables']]} "
                  f"done={s['doneCount']} send={s['sendVisible']} stop={s['stopVisible']} "
                  f"ai={[(a['outputting'], a['textLen']) for a in s['ai']]}", flush=True)
            if s["t"] > 8 and stable >= 6 and s["sendVisible"] and not s["stopVisible"]:
                break

        final = await page.evaluate(STATE_JS)
        (DUMP_DIR / "80_writing_samples.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "80_writing_final.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "80_writing_final.html").write_text(
            await page.content(), encoding="utf-8"
        )
        await page.screenshot(path=str(DUMP_DIR / "80_writing_final.png"))
        print("[writing] final url:", page.url, flush=True)
        for b in final["buttons"]:
            print("  button:", b, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
