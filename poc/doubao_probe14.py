"""Doubao image-ratio probe #14 (calibration aid, not a test).

Reproduces the gateway's `select_image_ratio` sequence in the image skill and
dumps what the ratio control actually opens when '1:1' is reported missing.

    uv run python poc/doubao_probe14.py

Dumps land in poc/dump/doubao/ratio/.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"
RATIO_CONTROL = 'button:has(span:text("比例"))'
POPUP_MENU = '[role="menu"]'
POPUP_ITEM = '[role="menu"] [role="menuitem"], [role="menu"] [role="menuitemcheckbox"]'

CONTROLS_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  return Array.from(document.querySelectorAll('button'))
    .filter((b) => /比例/.test(b.innerText))
    .map((b, i) => ({
      i,
      text: clean(b.innerText),
      cls: String(b.className || '').slice(0, 140),
      visible: !!(b.offsetWidth || b.offsetHeight || b.getClientRects().length),
    }));
}
"""

POPUPS_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  return Array.from(document.querySelectorAll('[role="menu"], [class*="dropdown"]'))
    .filter((el) => el.querySelector('[role^="menuitem"]'))
    .map((el, i) => ({
      i,
      role: el.getAttribute('role') || '',
      visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
      items: Array.from(el.querySelectorAll('[role^="menuitem"]')).map((it) => clean(it.innerText)),
    }));
}
"""


async def main() -> None:
    out_dir = DUMP_ROOT / "ratio"
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
        await page.goto("https://www.doubao.com/chat/", wait_until="domcontentloaded")
        await asyncio.sleep(6)

        # enter the image skill exactly like prepare()
        skill = page.locator('[data-testid="skill_bar_button_3"]')
        if await skill.count():
            await skill.first.click(force=True)
            await asyncio.sleep(3)
        await asyncio.sleep(1)

        controls = await page.evaluate(CONTROLS_JS)
        print("[ratio] controls matching 比例:", json.dumps(controls, ensure_ascii=False),
              flush=True)

        control = page.locator(RATIO_CONTROL).first
        print("[ratio] locator count:", await control.count(), flush=True)
        await control.click(timeout=8000)
        await asyncio.sleep(2)

        popups = await page.evaluate(POPUPS_JS)
        (out_dir / "popups.json").write_text(
            json.dumps(popups, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("[ratio] popups:", json.dumps(popups, ensure_ascii=False)[:600], flush=True)

        items = page.locator(POPUP_ITEM)
        n = await items.count()
        labels = [await items.nth(i).inner_text() for i in range(n)]
        print("[ratio] POPUP_ITEM count:", n, "labels:", labels, flush=True)

        for name in ("1:1", "自动"):
            exact = items.filter(has_text=re.compile(f"^{re.escape(name)}$"))
            loose = items.filter(has_text=name)
            print(f"[ratio] {name!r}: exact={await exact.count()} loose={await loose.count()}",
                  flush=True)

        (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")
        print("[ratio] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
