"""Check whether re-entering a mode keeps the existing conversation."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
BASE = "https://yuanbao.tencent.com/chat/naQivTmsDa/"
CONVS = {
    "search": BASE + "0RRMzaQ1QY4",
    "image": BASE + "0RRNL7U2xI8",
    "writing": BASE + "0RRNRZs6hcG",
}

STATE_JS = """
() => {
  const editor = document.querySelector('.ql-editor');
  return {
    url: location.href,
    placeholder: editor ? (editor.getAttribute('data-placeholder') || '') : '',
    items: document.querySelectorAll('.agent-chat__list__item').length,
    markdowns: Array.from(document.querySelectorAll('.hyc-common-markdown'))
      .map(el => (el.innerText || '').length),
    imgs: Array.from(document.querySelectorAll('.agent-chat__list__item--ai img'))
      .filter(el => el.getBoundingClientRect().width >= 100).length,
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
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        for name, url in CONVS.items():
            await page.goto(url, wait_until="domcontentloaded")
            await page.locator(".ql-editor").first.wait_for(state="visible", timeout=30000)
            await asyncio.sleep(2.5)
            before = await page.evaluate(STATE_JS)
            await page.screenshot(path=str(DUMP_DIR / f"91_{name}_before_switch.png"))

            if name == "search":
                await js_click(page, "button[aria-label='Add']")
                await asyncio.sleep(0.7)
                await js_click_text(page, "[role='menuitem']", "Search")
            elif name == "image":
                await js_click(page, "button[aria-label='AI Image']")
            else:
                await js_click(page, "button[aria-label='Writing']")
            await asyncio.sleep(2.5)

            after = await page.evaluate(STATE_JS)
            payload = {"before": before, "after": after}
            (DUMP_DIR / f"91_{name}_switch.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await page.screenshot(path=str(DUMP_DIR / f"91_{name}_after_switch.png"))
            print(f"[{name}] before={before}", flush=True)
            print(f"[{name}] after ={after}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
