"""Doubao video-result probe #13 (calibration aid, not a test).

Renders the finished clip: hover the video card (hover actions), dump the
hover buttons, click the play icon and capture the media response so the
driver knows how to obtain the MP4.

    uv run python poc/doubao_probe13.py --url "https://www.doubao.com/chat/<id>"

Dumps land in poc/dump/doubao/video/play/.
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

CARD_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const blocks = Array.from(document.querySelectorAll('[class*="block-video"]'));
  return blocks.map((b, i) => ({
    index: i,
    html: (b.outerHTML || '').slice(0, 12000),
    buttons: Array.from(b.querySelectorAll('button, [role="button"], [data-testid]')).map((el) => ({
      tag: el.tagName.toLowerCase(),
      testid: el.getAttribute('data-testid') || '',
      aria: el.getAttribute('aria-label') || '',
      text: clean(el.innerText).slice(0, 40),
      cls: String(el.className || '').slice(0, 90),
    })),
  }));
}
"""

AFTER_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const videos = Array.from(document.querySelectorAll('video')).map((v) => ({
    src: (v.currentSrc || v.src || '').slice(0, 600),
    poster: (v.poster || '').slice(0, 300),
    readyState: v.readyState,
  }));
  const btns = Array.from(document.querySelectorAll('button, [role="button"]')).map((b) => ({
    testid: b.getAttribute('data-testid') || '',
    aria: b.getAttribute('aria-label') || '',
    text: clean(b.innerText).slice(0, 30),
  })).filter((b) => /下载|download|保存|save/i.test(b.text + b.aria));
  return {videos, buttons: btns, url: location.href};
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    args = ap.parse_args()

    out_dir = DUMP_ROOT / "video" / "play"
    out_dir.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("HEADLESS") == "1"
    net: list[str] = []
    media: list[str] = []

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
            net.append(f"{resp.status} {ct} {url}")
            if re.search(r"video|mp4|media|seedance|doubaocdn", url + " " + ct, re.I):
                media.append(f"{resp.status} {ct} {url}")

        page.on("response", _on_response)
        try:
            await page.goto(args.url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[play] goto failed: {exc}", flush=True)
        await asyncio.sleep(10)

        cards = await page.evaluate(CARD_JS)
        (out_dir / "cards.json").write_text(
            json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[play] video cards: {len(cards)}", flush=True)
        for card in cards:
            for b in card["buttons"]:
                print(f"   card{card['index']} button testid={b['testid']!r} "
                      f"aria={b['aria']!r} text={b['text']!r} cls={b['cls']!r}", flush=True)

        blocks = page.locator('[class*="block-video"]')
        if await blocks.count():
            await blocks.first.hover(force=True)
            await asyncio.sleep(2)
            (out_dir / "hover.json").write_text(
                json.dumps(await page.evaluate(CARD_JS), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # click the play icon and capture whatever media the player fetches
        play = page.locator('[data-video-conner-tag="true"]')
        if await play.count():
            try:
                await play.first.click(force=True, timeout=8000)
                print("[play] clicked play icon", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[play] play click failed: {exc}", flush=True)
            await asyncio.sleep(12)

        state = await page.evaluate(AFTER_JS)
        (out_dir / "01_after.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "01_after.html").write_text(await page.content(), encoding="utf-8")
        print("[play] videos after click:", json.dumps(state["videos"], ensure_ascii=False)[:400],
              flush=True)
        print("[play] download buttons:", state["buttons"], flush=True)

        # hover actions again after playback
        if await blocks.count():
            await blocks.first.hover(force=True)
            await asyncio.sleep(2)
            state2 = await page.evaluate(AFTER_JS)
            print("[play] download buttons on hover:", state2["buttons"], flush=True)

        (out_dir / "media.log").write_text("\n".join(media), encoding="utf-8")
        print(f"[play] media hits: {len(media)}", flush=True)
        for line in media[:8]:
            print("   media:", line[:240], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
