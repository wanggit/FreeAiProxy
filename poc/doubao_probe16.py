"""Doubao video-controls probe #16 (calibration aid, not a test).

Prints every button in the video composer with its inner text, so the merged
比例·时长 trigger can be pinned down (its label does not contain 比例).

    uv run python poc/doubao_probe16.py

Dumps land in poc/dump/doubao/video/ctrl/.
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

BUTTONS_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const composer = document.querySelector('[data-testid="chat_input"]');
  if (!composer) return [];
  return Array.from(composer.querySelectorAll('button')).map((b, i) => ({
    i,
    text: clean(b.innerText).slice(0, 60),
    aria: b.getAttribute('aria-haspopup') || '',
    key: b.getAttribute('data-input-engine-actionbar-control-key') || '',
    testid: b.getAttribute('data-testid') || '',
    slot: b.getAttribute('data-slot') || '',
    visible: !!(b.offsetWidth || b.offsetHeight || b.getClientRects().length),
  }));
}
"""


async def main() -> None:
    out_dir = DUMP_ROOT / "video" / "ctrl"
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
            print(f"[ctrl] goto failed: {exc}", flush=True)
        await asyncio.sleep(5)
        await enter_skill(page, "video")
        await asyncio.sleep(4)

        buttons = await page.evaluate(BUTTONS_JS)
        (out_dir / "buttons.json").write_text(
            json.dumps(buttons, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for b in buttons:
            print(f"  [{b['i']}] text={b['text']!r} key={b['key']!r} "
                  f"slot={b['slot']!r} aria={b['aria']!r} testid={b['testid']!r}",
                  flush=True)
        print("[ctrl] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
