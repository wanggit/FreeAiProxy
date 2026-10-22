"""Find the reliable interaction path for selecting Hy4 preview."""

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
  const switchBtn = document.querySelector("button[aria-label='Switch model']");
  const modelsRow = document.querySelector("button[aria-label='Select model']");
  const radios = Array.from(document.querySelectorAll('[role="menuitemradio"]')).filter(visible)
    .map(el => ({
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim(),
      checked: el.getAttribute('aria-checked'),
      disabled: el.getAttribute('aria-disabled'),
      cls: String(el.className || '').slice(0, 160),
    }));
  return {
    mode: switchBtn ? (switchBtn.innerText || '').trim() : null,
    modelsRow: modelsRow ? (modelsRow.innerText || '').replace(/\\s+/g, ' ').trim() : null,
    radios,
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


async def open_menu(page) -> None:
    await page.locator("button[aria-label='Switch model']").first.click()
    await asyncio.sleep(0.7)


async def open_models_submenu(page) -> bool:
    row = page.get_by_role("menuitem", name="Select model")
    if await row.count() == 0:
        return False
    await row.first.hover()
    await asyncio.sleep(0.6)
    if await page.locator("[role='menuitemradio']", has_text="Hy4 preview").count() == 0:
        await row.first.click()
        await asyncio.sleep(1.0)
    return await page.locator("[role='menuitemradio']", has_text="Hy4 preview").count() > 0


async def select_mode(page, name: str) -> bool:
    await open_menu(page)
    loc = page.get_by_role("menuitemradio", name=re.compile(rf"^{name}"))
    if await loc.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await loc.first.click()
    await asyncio.sleep(1.0)
    return True


async def select_hy4(page) -> bool:
    await open_menu(page)
    if not await open_models_submenu(page):
        print("[select] submenu did not open", flush=True)
        await page.keyboard.press("Escape")
        return False
    loc = page.get_by_role("menuitemradio", name=re.compile(r"^Hy4 preview"))
    count = await loc.count()
    print("[select] Hy4 candidates:", count, flush=True)
    if count == 0:
        await page.keyboard.press("Escape")
        return False
    await loc.first.click()
    await asyncio.sleep(1.5)
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
        await asyncio.sleep(1.5)

        print("[select] state before:", await page.evaluate(STATE_JS), flush=True)

        print("[select] force Expert:", await select_mode(page, "Expert"), flush=True)
        print("[select] select Hy4 in Expert:", await select_hy4(page), flush=True)
        after_expert = await page.evaluate(STATE_JS)
        print("[select] after Hy4/Expert:", after_expert, flush=True)
        await open_menu(page)
        await page.screenshot(path=str(DUMP_DIR / "B0_hy4_expert_menu.png"))
        (DUMP_DIR / "B0_hy4_expert_menu.json").write_text(
            json.dumps(await page.evaluate(STATE_JS), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

        print("[select] force Instant:", await select_mode(page, "Instant"), flush=True)
        print("[select] select Hy4 in Instant:", await select_hy4(page), flush=True)
        after_instant = await page.evaluate(STATE_JS)
        print("[select] after Hy4/Instant:", after_instant, flush=True)
        await page.screenshot(path=str(DUMP_DIR / "B1_hy4_instant.png"))


if __name__ == "__main__":
    asyncio.run(main())
