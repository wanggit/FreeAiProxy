"""Doubao video-confirm probe #11 (calibration aid, not a test).

The video skill agent asks for confirmation in prose; replying 确认 starts the
render. This probe sends that confirmation and polls until a <video> element
appears, dumping the answer DOM and the media URLs on the way.

    uv run python poc/doubao_probe11.py --url "https://www.doubao.com/chat/<id>"

Dumps land in poc/dump/doubao/video/render/.
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
  let streaming = false;
  if (last) {
    text = clean(last.innerText).slice(0, 500);
    streaming = !!last.querySelector('[data-testid="message_text_content"][data-streaming="true"]');
    for (const v of last.querySelectorAll('video')) {
      videos.push({src: (v.currentSrc || v.src || '').slice(0, 600),
                   cls: String(v.className || '').slice(0, 80),
                   poster: (v.poster || '').slice(0, 300)});
    }
  }
  return {url: location.href, aiCount: ais.length, text, streaming, videos};
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--confirm", default="确认")
    ap.add_argument("--wait", type=int, default=900)
    args = ap.parse_args()

    out_dir = DUMP_ROOT / "video" / "render"
    out_dir.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("HEADLESS") == "1"
    net: list[str] = []

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        humanize=True,
        os="macos",
        locale="zh-CN",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        def _on_response(resp):  # noqa: ANN001, ANN202
            url = resp.url
            ct = resp.headers.get("content-type", "")
            if re.search(r"video|mp4|media|seedance|v-?[0-9]+-video", url + ct, re.I):
                net.append(f"{resp.status} {ct} {url}")

        page.on("response", _on_response)
        try:
            await page.goto(args.url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[render] goto failed: {exc}", flush=True)
        await asyncio.sleep(6)

        state = await page.evaluate(STATE_JS)
        before = state["aiCount"]
        print(f"[render] ai={before} videos={len(state['videos'])} "
              f"streaming={state['streaming']}", flush=True)

        editor = page.locator('[data-testid="chat_input_input"] [contenteditable="true"]')
        await editor.first.click()
        await page.keyboard.type(args.confirm, delay=20)
        await asyncio.sleep(0.5)
        send = page.locator('[data-testid="chat_input_send_button"]')
        if await send.count():
            await send.first.click()
        else:
            await page.keyboard.press("Enter")
        print(f"[render] sent {args.confirm!r}", flush=True)

        t = 0
        while t < args.wait:
            await asyncio.sleep(10)
            t += 10
            state = await page.evaluate(STATE_JS)
            (out_dir / f"tick_{t:04d}.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[tick_{t:04d}] ai={state['aiCount']} videos={len(state['videos'])} "
                  f"streaming={state['streaming']} text={state['text'][:70]!r}", flush=True)
            if state["videos"]:
                (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")
                break

        (out_dir / "net.log").write_text("\n".join(net), encoding="utf-8")
        print(f"[render] done; {len(net)} network hits", flush=True)
        for line in net[:10]:
            print("   net:", line[:200], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
