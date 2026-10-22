"""Open Yuanbao Presentation Maker and inspect its input UI."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
START_URL = "https://yuanbao.tencent.com/"

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
    aria: el.getAttribute('aria-label') || '',
    placeholder: el.getAttribute('placeholder') || '',
    cls: String(el.className || '').slice(0, 180),
  });
  return {
    url: location.href,
    title: document.title,
    bodyText: (document.body.innerText || '').slice(0, 8000),
    buttons: Array.from(document.querySelectorAll('button,[role="button"],a')).filter(visible)
      .map(desc).filter(x => x.text || x.aria).slice(0, 200),
    inputs: Array.from(document.querySelectorAll(
        'textarea,input,[contenteditable="true"],[contenteditable=""],[role="textbox"]'))
      .filter(visible).map(desc).slice(0, 40),
    fileInputs: Array.from(document.querySelectorAll('input[type="file"]')).map(el => ({
      accept: el.accept || '', multiple: el.multiple,
    })),
    iframes: Array.from(document.querySelectorAll('iframe')).filter(visible)
      .map(el => (el.src || '').slice(0, 200)),
  };
}
"""


async def open_menu(page) -> None:
    await page.locator("button[aria-label='Switch model']").first.click()
    await asyncio.sleep(0.7)


async def select_model(page, prefix: str) -> bool:
    await open_menu(page)
    row = page.get_by_role("menuitem", name="Select model")
    if await row.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await row.first.click()
    await asyncio.sleep(1.0)
    item = page.get_by_role("menuitemradio", name=re.compile(rf"^{prefix}"))
    if await item.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await item.first.click()
    await asyncio.sleep(1.3)
    return True


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
        await asyncio.sleep(1.2)

        print("[ppt] select Hy4:", await select_model(page, "Hy4 preview"), flush=True)

        clicked = bool(await page.evaluate(
            """() => {
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none';
                };
                const els = Array.from(document.querySelectorAll('button,[role="button"],div,span'))
                    .filter(visible)
                    .filter(el => (el.innerText || '').replace(/\\s+/g, ' ').trim() === 'Presentation Maker');
                if (!els.length) return false;
                els[els.length - 1].click();
                return true;
            }"""
        ))
        print("[ppt] clicked Presentation Maker:", clicked, flush=True)
        await asyncio.sleep(3.0)

        info = await page.evaluate(STATE_JS)
        (DUMP_DIR / "F0_ppt_mode.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "F0_ppt_mode.html").write_text(
            await page.content(), encoding="utf-8"
        )
        await page.screenshot(path=str(DUMP_DIR / "F0_ppt_mode.png"))
        print("[ppt] url:", info["url"], flush=True)
        print("[ppt] inputs:", info["inputs"], flush=True)
        print("[ppt] buttons:", flush=True)
        for b in info["buttons"][:60]:
            print("  ", b, flush=True)
        print("[ppt] body:", info["bodyText"][:2500], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
