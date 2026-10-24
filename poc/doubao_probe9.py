"""Doubao video-flow probe #9 (calibration aid, not a test).

The video skill is an agent: after the first prompt it replies with a
parameter confirmation ("确认后我再开始生成视频") and only then starts rendering.
This probe re-opens that conversation, finds the confirmation control, clicks
it (or replies 确认) and polls until a <video> element shows up, dumping the
answer DOM on the way.

    uv run python poc/doubao_probe9.py --url "https://www.doubao.com/chat/<id>"

Dumps land in poc/dump/doubao/video/flow/.
"""

from __future__ import annotations

import argparse
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

STATE_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const ais = Array.from(document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]'));
  const last = ais.length ? ais[ais.length - 1] : null;
  const videos = [];
  let text = '';
  if (last) {
    text = clean(last.innerText).slice(0, 400);
    for (const v of last.querySelectorAll('video')) {
      videos.push({src: (v.currentSrc || v.src || '').slice(0, 500), cls: String(v.className).slice(0, 80)});
    }
  }
  const buttons = Array.from(document.querySelectorAll('button')).map((b) => ({
    text: clean(b.innerText).slice(0, 30),
    aria: b.getAttribute('aria-label') || '',
    testid: b.getAttribute('data-testid') || '',
  })).filter((b) => /确认|开始|生成|继续|重试|retry|confirm/i.test(b.text + b.aria));
  return {url: location.href, aiCount: ais.length, text, videos, buttons};
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--wait", type=int, default=600)
    args = ap.parse_args()

    out_dir = DUMP_ROOT / "video" / "flow"
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
            print(f"[flow] goto failed: {exc}", flush=True)
        await asyncio.sleep(6)

        state = await page.evaluate(STATE_JS)
        (out_dir / "00_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "00_state.html").write_text(await page.content(), encoding="utf-8")
        print(f"[flow] ai={state['aiCount']} videos={len(state['videos'])} "
              f"buttons={state['buttons']}", flush=True)
        print(f"[flow] text: {state['text'][:160]}", flush=True)

        if not state["videos"]:
            clicked = False
            for b in state["buttons"]:
                sel = f'[data-testid="{b["testid"]}"]' if b["testid"] else None
                loc = page.locator(sel) if sel else None
                try:
                    if loc is None or not await loc.count():
                        loc = page.get_by_role("button", name=re.compile(b["text"]))
                    if await loc.count():
                        await loc.first.click(force=True, timeout=8000)
                        print(f"[flow] clicked {b['text']!r}", flush=True)
                        clicked = True
                        break
                except Exception as exc:  # noqa: BLE001
                    print(f"[flow] click {b['text']!r} failed: {exc}", flush=True)
            if not clicked:
                editor = page.locator(
                    '[data-testid="chat_input_input"] [contenteditable="true"]'
                )
                if await editor.count():
                    await editor.first.click()
                    await page.keyboard.type("确认", delay=20)
                    await page.keyboard.press("Enter")
                    print("[flow] typed 确认", flush=True)

        t = 0
        while t < args.wait:
            await asyncio.sleep(5)
            t += 5
            state = await page.evaluate(STATE_JS)
            (out_dir / f"tick_{t:04d}.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[tick_{t:04d}] videos={len(state['videos'])} text={state['text'][:90]!r}",
                  flush=True)
            if state["videos"]:
                (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")
                break
        print("[flow] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
