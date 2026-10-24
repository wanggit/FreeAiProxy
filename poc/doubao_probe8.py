"""Doubao music-URL probe #8 (calibration aid, not a test).

Opens a finished music conversation, clicks the card's play button and
captures the `/alice/media/bigmusic/get_video` JSON plus every media URL the
player materialises, so the driver knows exactly how to recover the track.

    uv run python poc/doubao_probe8.py --url "https://www.doubao.com/chat/<id>"

Dumps land in poc/dump/doubao/music/url/.
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

MEDIA_JS = """
() => ({
  audio: Array.from(document.querySelectorAll('audio')).map((a) => (a.currentSrc || a.src || '')),
  video: Array.from(document.querySelectorAll('video')).map((v) => ({
    src: (v.currentSrc || v.src || '').slice(0, 700),
    cls: String(v.className || '').slice(0, 120),
    duration: v.duration || 0,
  })),
  res: performance.getEntriesByType('resource')
    .filter((r) => /videoweb|byteimg\\/.*video|media\\/|bigmusic|\\.mp4|\\.mp3|\\.m4a|\\.flac/i.test(r.name))
    .map((r) => r.name.slice(0, 700)),
})
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--wait", type=int, default=30)
    args = ap.parse_args()

    out_dir = DUMP_ROOT / "music" / "url"
    out_dir.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("HEADLESS") == "1"
    api_hits: list[str] = []

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        humanize=True,
        os="macos",
        locale="zh-CN",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        async def _capture(resp):  # noqa: ANN001, ANN202
            if "/alice/media/" in resp.url or "bigmusic" in resp.url:
                try:
                    body = await resp.text()
                except Exception as exc:  # noqa: BLE001
                    body = f"<unreadable: {exc}>"
                api_hits.append(resp.url)
                (out_dir / f"api_{len(api_hits):02d}.json").write_text(
                    json.dumps(
                        {"url": resp.url, "status": resp.status, "body": body},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"[url] api hit {resp.status} {resp.url[:120]}", flush=True)

        page.on("response", lambda r: asyncio.ensure_future(_capture(r)))
        try:
            await page.goto(args.url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[url] goto failed: {exc}", flush=True)
        await asyncio.sleep(6)

        play = page.locator('[class*="play-button-wrapper"]')
        if await play.count():
            await play.last.click(force=True, timeout=8000)
            print("[url] clicked play", flush=True)

        for i in range(args.wait // 3):
            await asyncio.sleep(3)
            info = await page.evaluate(MEDIA_JS)
            (out_dir / f"tick_{i:02d}.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                f"[tick_{i:02d}] audio={len(info['audio'])} video={len(info['video'])} "
                f"res={len(info['res'])}",
                flush=True,
            )
            if info["video"]:
                for v in info["video"]:
                    print("   video:", v["src"][:200], flush=True)
                break
        (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")
        print(f"[url] done; {len(api_hits)} api hits", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
