"""Probe Yuanbao web search toggle and searched-answer DOM."""

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
PROMPT = "请联网查询今天杭州的天气和空气质量，简短回答，并给出来源。"

SAMPLE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const markdowns = Array.from(document.querySelectorAll('.hyc-common-markdown')).filter(visible)
    .map(el => ({
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 300),
      done: !!el.closest('.hyc-content-md-done'),
      cls: String(el.parentElement?.className || '').slice(0, 120),
    }));
  const searchish = Array.from(document.querySelectorAll(
      '[class*="search"],[class*="Search"],[class*="source"],[class*="citation"],[class*="reference"]'))
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 120),
      len: (el.innerText || '').length,
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
    }))
    .filter(x => x.text).slice(0, 80);
  const aria = Array.from(document.querySelectorAll('[aria-label]')).filter(visible)
    .map(el => el.getAttribute('aria-label'))
    .filter(x => x && /send|stop|pause|copy|regenerate|like|dislike|share/i.test(x));
  return {
    url: location.href,
    bodyLen: (document.body.innerText || '').length,
    markdowns,
    searchish,
    aria: [...new Set(aria)],
    aiItems: document.querySelectorAll('.agent-chat__list__item--ai').length,
    humanItems: document.querySelectorAll('.agent-chat__list__item--human').length,
  };
}
"""


async def click_visible(page, name: str, role: str = "button") -> bool:
    loc = page.get_by_role(role, name=name, exact=True)
    for i in range(await loc.count()):
        item = loc.nth(i)
        if await item.is_visible():
            await item.click()
            await asyncio.sleep(0.6)
            return True
    return False


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


async def js_click_text(page, selector: str, text: str) -> bool:
    return bool(await page.evaluate(
        """([sel, want]) => {
            const els = Array.from(document.querySelectorAll(sel));
            const el = els.find(e => (e.innerText || '').replace(/\\s+/g, ' ').trim() === want
                && e.getBoundingClientRect().width > 0);
            if (!el) return false;
            el.click();
            return true;
        }""",
        [selector, text],
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
        await page.locator("button[aria-label='Add']").first.wait_for(
            state="visible", timeout=30000
        )
        await asyncio.sleep(2.0)
        await js_click_text(page, "*", "Got It")
        await asyncio.sleep(0.4)

        print("[search] open Add", await js_click(page, "button[aria-label='Add']"), flush=True)
        await asyncio.sleep(0.8)
        print(
            "[search] click Search menuitem",
            await js_click_text(page, "[role='menuitem']", "Search"),
            flush=True,
        )
        await asyncio.sleep(0.8)
        await page.screenshot(path=str(DUMP_DIR / "40_search_on.png"))
        on_info = await page.evaluate(SAMPLE_JS)
        (DUMP_DIR / "40_search_on.json").write_text(
            json.dumps(on_info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("[search] toggle body:", on_info["bodyLen"], flush=True)
        for item in on_info["searchish"][:10]:
            print("  searchish:", item, flush=True)

        await page.locator(".ql-editor").first.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.4)
        await page.locator("[aria-label='Send']").first.click()
        print("[search] sent", flush=True)

        t0 = time.monotonic()
        samples = []
        last_md = ""
        stable = 0
        while time.monotonic() - t0 < 120:
            await asyncio.sleep(0.5)
            s = await page.evaluate(SAMPLE_JS)
            s["t"] = round(time.monotonic() - t0, 2)
            samples.append(s)
            md = "|".join(m["text"] for m in s["markdowns"])
            if md == last_md:
                stable += 1
            else:
                stable = 0
                last_md = md
            print(f"[search] t={s['t']:5.1f} body={s['bodyLen']:5} ai={s['aiItems']} "
                  f"md={[(m['len'], m['done']) for m in s['markdowns']]} "
                  f"aria={s['aria']}", flush=True)
            if samples[-1]["t"] > 8 and s["markdowns"] and stable >= 8:
                break

        (DUMP_DIR / "40_search_samples.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "40_search_final.html").write_text(
            await page.content(), encoding="utf-8"
        )
        await page.screenshot(path=str(DUMP_DIR / "40_search_final.png"))
        print("[search] final url:", page.url, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
