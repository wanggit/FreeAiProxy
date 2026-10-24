"""Doubao video-duration probe #18 (calibration aid, not a test).

The gateway's thumb.click() hangs (the span is never "visible"), so this probe
compares ways of moving the radix duration slider: JS focus + arrow keys,
force click + arrow keys, and Home/End.

    uv run python poc/doubao_probe18.py

Dumps land in poc/dump/doubao/video/duration/.
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
THUMB = '[role="slider"][data-slot="slider-thumb"]'

THUMB_STATE_JS = """
() => {
  const t = document.querySelector('[role="slider"][data-slot="slider-thumb"]');
  if (!t) return null;
  const r = t.getBoundingClientRect();
  return {
    val: t.getAttribute('aria-valuenow'),
    focused: document.activeElement === t,
    rect: {w: Math.round(r.width), h: Math.round(r.height),
           x: Math.round(r.x), y: Math.round(r.y)},
    panel: !!t.closest('[role="menu"]'),
  };
}
"""


async def _open(page) -> None:  # noqa: ANN001
    trigger = page.locator(TRIGGER).first
    await trigger.click(timeout=8000)
    await page.locator('[role="menu"]').first.wait_for(state="visible", timeout=8000)
    await asyncio.sleep(1)


async def main() -> None:
    out_dir = DUMP_ROOT / "video" / "duration"
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
            print(f"[dur] goto failed: {exc}", flush=True)
        await asyncio.sleep(5)
        await enter_skill(page, "video")
        await asyncio.sleep(3)

        print("[dur] trigger count:", await page.locator(TRIGGER).count(), flush=True)
        await _open(page)
        state = await page.evaluate(THUMB_STATE_JS)
        print("[dur] after open:", json.dumps(state), flush=True)

        # strategy A: JS focus + arrows
        done = await page.evaluate(
            "() => { const t = document.querySelector("
            "'[role=\"slider\"][data-slot=\"slider-thumb\"]');"
            " if (!t) return false; t.focus(); return document.activeElement === t; }"
        )
        print("[dur] js focus:", done, flush=True)
        await page.keyboard.press("ArrowLeft")
        await asyncio.sleep(0.4)
        print("[dur] after ArrowLeft:", json.dumps(await page.evaluate(THUMB_STATE_JS)),
              flush=True)
        for _ in range(4):
            await page.keyboard.press("ArrowRight")
            await asyncio.sleep(0.15)
        await asyncio.sleep(0.5)
        print("[dur] after 4 ArrowRight:", json.dumps(await page.evaluate(THUMB_STATE_JS)),
              flush=True)
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)

        # strategy B: force click + arrows
        await _open(page)
        thumb = page.locator(THUMB).first
        await thumb.click(force=True, timeout=8000)
        await asyncio.sleep(0.3)
        print("[dur] after force click:", json.dumps(await page.evaluate(THUMB_STATE_JS)),
              flush=True)
        await page.keyboard.press("ArrowRight")
        await asyncio.sleep(0.5)
        print("[dur] ArrowRight after click:", json.dumps(await page.evaluate(THUMB_STATE_JS)),
              flush=True)
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        trigger = page.locator(TRIGGER).first
        print("[dur] trigger text:", await trigger.inner_text(), flush=True)
        (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")
        print("[dur] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
