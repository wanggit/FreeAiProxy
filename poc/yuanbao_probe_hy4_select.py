"""Reproduce the known-good model submenu path and select Hy4."""

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

MENU_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const switchBtn = document.querySelector("button[aria-label='Switch model']");
  const radios = Array.from(document.querySelectorAll('[role="menuitemradio"]')).filter(visible)
    .map(el => ({
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
      checked: el.getAttribute('aria-checked'),
      disabled: el.getAttribute('aria-disabled'),
    }));
  return {
    mode: switchBtn ? (switchBtn.innerText || '').trim() : null,
    radios,
  };
}
"""


async def ensure_mode(page, name: str) -> bool:
    await page.locator("button[aria-label='Switch model']").first.click()
    await asyncio.sleep(0.6)
    loc = page.get_by_role("menuitemradio", name=re.compile(rf"^{name}"))
    if await loc.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await loc.first.click()
    await asyncio.sleep(0.8)
    return True


async def main() -> None:
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

        await ensure_mode(page, "Instant")
        print("[hy4-select] base state:", await page.evaluate(MENU_JS), flush=True)

        await page.locator("button[aria-label='Switch model']").first.click()
        await asyncio.sleep(0.7)
        row = page.get_by_role("menuitem", name="Select model")
        print("[hy4-select] Models row count:", await row.count(), flush=True)
        await page.screenshot(path=str(DUMP_DIR / "C0_menu_open.png"))
        await row.first.click()
        await asyncio.sleep(1.2)
        submenu = await page.evaluate(MENU_JS)
        print("[hy4-select] submenu:", submenu, flush=True)
        (DUMP_DIR / "C1_submenu.json").write_text(
            json.dumps(submenu, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        await page.screenshot(path=str(DUMP_DIR / "C1_submenu.png"))

        hy4 = page.get_by_role("menuitemradio", name=re.compile(r"^Hy4 preview"))
        print("[hy4-select] Hy4 count:", await hy4.count(), flush=True)
        if await hy4.count():
            await hy4.first.click()
            await asyncio.sleep(1.5)
            print("[hy4-select] after click:", await page.evaluate(MENU_JS), flush=True)
            await page.screenshot(path=str(DUMP_DIR / "C2_hy4_after.png"))

            await page.locator("button[aria-label='Switch model']").first.click()
            await asyncio.sleep(0.8)
            open_state = await page.evaluate(MENU_JS)
            print("[hy4-select] menu reopened:", open_state, flush=True)
            (DUMP_DIR / "C2_hy4_menu.json").write_text(
                json.dumps(open_state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await page.screenshot(path=str(DUMP_DIR / "C2_hy4_menu.png"))
            await page.keyboard.press("Escape")


if __name__ == "__main__":
    asyncio.run(main())
