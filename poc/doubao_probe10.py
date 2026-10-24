"""Doubao video-confirm probe #10 (calibration aid, not a test).

Dumps the full DOM of the video agent's confirmation message plus every
interactive element on the page, so the control that actually starts the
render can be identified.

    uv run python poc/doubao_probe10.py --url "https://www.doubao.com/chat/<id>"

Dumps land in poc/dump/doubao/video/confirm/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"

CONFIRM_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const ais = Array.from(document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]'));
  const last = ais.length ? ais[ais.length - 1] : null;
  const interactive = Array.from(document.querySelectorAll(
      'button, [role="button"], [data-testid], [class*="confirm"], [class*="Confirm"]'))
    .map((el) => ({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      testid: el.getAttribute('data-testid') || '',
      text: clean(el.innerText).slice(0, 60),
      cls: String(el.className || '').slice(0, 90),
    }))
    .filter((el) => /确认|开始|继续|重试|生成视频|生成|retry|confirm/i.test(el.text))
    .slice(0, 40);
  return {
    url: location.href,
    aiCount: ais.length,
    lastHtml: last ? (last.outerHTML || '') : '',
    interactive,
  };
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--click", default="")
    args = ap.parse_args()

    out_dir = DUMP_ROOT / "video" / "confirm"
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
            await page.goto(args.url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[confirm] goto failed: {exc}", flush=True)
        await asyncio.sleep(6)

        state = await page.evaluate(CONFIRM_JS)
        (out_dir / "00_last.html").write_text(state.pop("lastHtml"), encoding="utf-8")
        (out_dir / "00_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "00_page.html").write_text(await page.content(), encoding="utf-8")
        print(f"[confirm] ai={state['aiCount']}", flush=True)
        for el in state["interactive"]:
            print(f"   {el['tag']} role={el['role']!r} testid={el['testid']!r} "
                  f"text={el['text']!r}", flush=True)

        if args.click:
            loc = page.locator(args.click)
            if await loc.count():
                await loc.first.click(force=True, timeout=8000)
                print(f"[confirm] clicked {args.click}", flush=True)
                await asyncio.sleep(8)
                after = await page.evaluate(CONFIRM_JS)
                (out_dir / "01_after.json").write_text(
                    json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"[confirm] after: ai={after['aiCount']} "
                      f"interactive={len(after['interactive'])}", flush=True)
        print("[confirm] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
