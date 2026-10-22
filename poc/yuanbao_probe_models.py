"""Enumerate Yuanbao's model submenu (models vs thinking modes)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
START_URL = "https://yuanbao.tencent.com/"

MENU_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const items = Array.from(document.querySelectorAll(
      '[role="menuitem"],[role="menuitemradio"],[role="option"],[role="menu"] *[class*="item"]'))
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      ariaChecked: el.getAttribute('aria-checked') || '',
      ariaSelected: el.getAttribute('aria-selected') || '',
      disabled: el.getAttribute('aria-disabled') || '',
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
      cls: String(el.className || '').slice(0, 200),
      html: el.outerHTML.slice(0, 1200),
    }))
    .filter(i => i.text && !i.text.startsWith('Models Hy'));
  return {
    url: location.href,
    items,
    bodyText: (document.body.innerText || '').slice(0, 3000),
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

        await page.locator("button[aria-label='Switch model']").first.click()
        await asyncio.sleep(0.6)
        await page.get_by_role("menuitem", name="Select model").click()
        await asyncio.sleep(1.0)

        info = await page.evaluate(MENU_JS)
        (DUMP_DIR / "20_model_submenu.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "20_model_submenu.html").write_text(
            await page.content(), encoding="utf-8"
        )
        await page.screenshot(
            path=str(DUMP_DIR / "20_model_submenu.png"), full_page=False
        )

        for item in info["items"]:
            print(
                f"{item['role']:15} checked={item['ariaChecked'] or '-':5} "
                f"text={item['text'][:100]!r}",
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
