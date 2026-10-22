"""Probe Yuanbao AI Image generation and result DOM/downloadability."""

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
PROMPT = "一只戴着红色围巾的橘猫坐在雪地里，水彩插画风格，画面干净"

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const imgs = Array.from(document.querySelectorAll('img')).filter(visible)
    .map(el => ({
      alt: el.alt || '',
      src: (el.currentSrc || el.src || '').slice(0, 500),
      w: Math.round(el.getBoundingClientRect().width),
      h: Math.round(el.getBoundingClientRect().height),
      cls: String(el.className || '').slice(0, 160),
    }))
    .filter(x => x.w >= 100 && x.h >= 100);
  const aria = Array.from(document.querySelectorAll('[aria-label]')).filter(visible)
    .map(el => el.getAttribute('aria-label'))
    .filter(x => x && /send|stop|pause|copy|download|regenerate|like|dislike|share|favorite/i.test(x));
  const resultish = Array.from(document.querySelectorAll(
      '[class*="image"],[class*="Image"],[class*="result"],[class*="Result"],[class*="card"],[class*="Card"]'))
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 180),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
    }))
    .filter(x => x.text || x.cls.toLowerCase().includes('image')).slice(0, 80);
  const ai = Array.from(document.querySelectorAll('.agent-chat__list__item--ai')).map(el => ({
    outputting: el.getAttribute('data-conv-outputting'),
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 500),
    htmlLen: el.outerHTML.length,
  }));
  return {
    url: location.href,
    bodyLen: (document.body.innerText || '').length,
    imgs,
    aria: [...new Set(aria)],
    resultish,
    ai,
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
        await page.locator("button[aria-label='AI Image']").first.wait_for(
            state="visible", timeout=30000
        )
        await asyncio.sleep(1.5)

        await js_click(page, "button[aria-label='AI Image']")
        await asyncio.sleep(2)
        await page.screenshot(path=str(DUMP_DIR / "70_image_mode.png"))

        await page.locator(".ql-editor").first.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.5)
        await page.locator("[aria-label='Send']").first.click()
        print("[image-gen] sent", flush=True)

        t0 = time.monotonic()
        samples = []
        last_sig = ""
        stable = 0
        while time.monotonic() - t0 < 240:
            await asyncio.sleep(1.0)
            s = await page.evaluate(STATE_JS)
            s["t"] = round(time.monotonic() - t0, 1)
            samples.append(s)
            sig = "|".join(i["src"] for i in s["imgs"]) + f"#{s['bodyLen']}"
            if sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            print(f"[image-gen] t={s['t']:5.1f} body={s['bodyLen']:5} imgs={len(s['imgs'])} "
                  f"ai={[(a['outputting'], len(a['text'])) for a in s['ai']]} "
                  f"send={s['sendVisible']} stop={s['stopVisible']} aria={s['aria']}", flush=True)
            if s["imgs"] and stable >= 5 and s["sendVisible"] and not s["stopVisible"]:
                break
            if s["t"] > 30 and stable >= 15 and s["sendVisible"] and not s["stopVisible"]:
                break

        final = await page.evaluate(STATE_JS)
        (DUMP_DIR / "70_image_samples.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "70_image_final.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "70_image_final.html").write_text(
            await page.content(), encoding="utf-8"
        )
        await page.screenshot(path=str(DUMP_DIR / "70_image_final.png"))

        print("[image-gen] final url:", page.url, flush=True)
        for img in final["imgs"]:
            src = img["src"]
            print(f"  img {img['w']}x{img['h']} {src[:120]}", flush=True)
            if src.startswith("http"):
                try:
                    resp = await context.request.get(src)
                    body = await resp.body()
                    print(f"    fetch: status={resp.status} ct={resp.headers.get('content-type')} "
                          f"bytes={len(body)}", flush=True)
                except Exception as exc:
                    print(f"    fetch failed: {type(exc).__name__}: {exc}", flush=True)
            elif src.startswith("blob:"):
                size = await page.evaluate(
                    """async (u) => {
                        const r = await fetch(u);
                        const b = await r.arrayBuffer();
                        return b.byteLength;
                    }""",
                    src,
                )
                print(f"    blob bytes={size}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
