"""Doubao duration-after-ratio probe #19 (calibration aid, not a test).

Replays the gateway order — pick ratio first, then set duration — and logs
the thumb/panel state after every step so the `-1` (panel gone) failure can be
localized.

    uv run python poc/doubao_probe19.py

Dumps land in poc/dump/doubao/video/dur19/.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "poc"))  # noqa: E402

from camoufox.async_api import AsyncCamoufox  # noqa: E402

from doubao_probe3 import START_URL, enter_skill  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"
TRIGGER = '[data-testid="chat_input"] button[data-slot="dropdown-menu-trigger"]:has-text("·")'
STATE_JS = """
() => {
  const t = document.querySelector('[role="slider"][data-slot="slider-thumb"]');
  const menu = document.querySelector('[role="menu"][data-state="open"]');
  return {
    thumb: t ? {val: t.getAttribute('aria-valuenow'),
                focused: document.activeElement === t,
                panel: !!t.closest('[role="menu"]')} : null,
    openMenu: !!menu,
    menuState: menu ? menu.getAttribute('data-state') : null,
    active: document.activeElement ?
      (document.activeElement.tagName + '.' +
       String(document.activeElement.className).slice(0, 60)) : null,
  };
}
"""


async def dump(page, tag: str) -> None:  # noqa: ANN001
    print(f"[{tag}]", json.dumps(await page.evaluate(STATE_JS), ensure_ascii=False),
          flush=True)


async def main() -> None:
    out_dir = DUMP_ROOT / "video" / "dur19"
    out_dir.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("HEADLESS") == "1"

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        humanize=True,
        os="macos",
        locale="zh-CN",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(START_URL, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[dur19] goto failed: {exc}", flush=True)
        await asyncio.sleep(5)
        await enter_skill(page, "video")
        await asyncio.sleep(3)

        trigger = page.locator(TRIGGER).first
        # step 1: pick ratio 16:9
        await trigger.click(timeout=8000)
        await page.locator('[role="menu"]').first.wait_for(state="visible", timeout=8000)
        await asyncio.sleep(1)
        item = page.locator('[role="menu"] button').filter(has_text="16:9")
        print("[dur19] ratio items:", await item.count(), flush=True)
        await item.first.click(force=True)
        await asyncio.sleep(1)
        await dump(page, "after_ratio")
        print("[dur19] trigger:", await trigger.inner_text(), flush=True)

        # step 2: open merged panel for duration
        await trigger.click(timeout=8000)
        try:
            await page.locator('[role="menu"]').first.wait_for(state="visible", timeout=8000)
        except Exception as exc:  # noqa: BLE001
            print("[dur19] menu reopen failed:", exc, flush=True)
        await asyncio.sleep(1)
        await dump(page, "panel_open")

        await page.evaluate(
            "() => { const t = document.querySelector("
            "'[role=\"slider\"][data-slot=\"slider-thumb\"]');"
            " if (t) t.focus(); }"
        )
        await asyncio.sleep(0.3)
        await dump(page, "after_focus")
        for i in range(6):
            await page.keyboard.press("ArrowRight")
            await asyncio.sleep(0.3)
            await dump(page, f"arrow_{i}")
        await asyncio.sleep(0.6)
        await dump(page, "final")
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.7)
        print("[dur19] trigger after:", await trigger.inner_text(), flush=True)
        (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")
        print("[dur19] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
