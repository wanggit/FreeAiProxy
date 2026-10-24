"""Doubao artifact probe #6 (calibration aid, not a test).

Re-opens an existing Doubao conversation and tries to recover the *raw*
artifact URL (audio track / video file / watermark-free image) by interacting
with the generated card and logging the network traffic plus the media
elements it materialises.

    uv run python poc/doubao_probe6.py music --url "https://www.doubao.com/chat/<id>"
    uv run python poc/doubao_probe6.py video --url "https://www.doubao.com/chat/<id>"
    uv run python poc/doubao_probe6.py image --url "https://www.doubao.com/chat/<id>"

Dumps land in poc/dump/doubao/<mode>/artifact/.
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

_MEDIA_JS = """
() => ({
  url: location.href,
  audio: Array.from(document.querySelectorAll('audio')).map((a) => ({
    src: (a.currentSrc || a.src || '').slice(0, 600),
    cls: String(a.className || '').slice(0, 120),
    duration: a.duration || 0,
  })),
  video: Array.from(document.querySelectorAll('video')).map((v) => ({
    src: (v.currentSrc || v.src || '').slice(0, 600),
    cls: String(v.className || '').slice(0, 120),
    poster: (v.poster || '').slice(0, 400),
    duration: v.duration || 0,
  })),
  res: performance.getEntriesByType('resource')
    .filter((r) => /\\.(mp3|flac|wav|ogg|m4a|mp4|webm|m3u8|jpeg|png|webp)(\\?|$)/i.test(r.name)
             || /audio|video\\/|music|rc_gen|rc\\/video|video_generation/i.test(r.name))
    .slice(-40)
    .map((r) => r.name.slice(0, 500)),
  lastText: (document.querySelectorAll('[data-testid="message_content"]') || [])
    .length ? '' : '',
})
"""

DL_JS = """
() => Array.from(document.querySelectorAll('button'))
  .map((b) => ({
    text: (b.innerText || '').trim().slice(0, 40),
    aria: b.getAttribute('aria-label') || '',
    title: b.getAttribute('title') || '',
    cls: String(b.className || '').slice(0, 120),
  }))
  .filter((b) => /download|下载|保存|save|export|导出/i.test(b.text + b.aria + b.title))
"""


async def _dump(page, out_dir: Path, tag: str) -> dict:  # noqa: ANN001
    info = await page.evaluate(_MEDIA_JS)
    info["dlButtons"] = await page.evaluate(DL_JS)
    (out_dir / f"{tag}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"{tag}.html").write_text(await page.content(), encoding="utf-8")
    print(f"[{tag}] audio={len(info['audio'])} video={len(info['video'])} "
          f"res={len(info['res'])} dl={len(info['dlButtons'])}", flush=True)
    for a in info["audio"]:
        print("   audio:", a["src"][:160], flush=True)
    for v in info["video"]:
        print("   video:", v["src"][:160], flush=True)
    return info


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["image", "music", "video"])
    ap.add_argument("--url", required=True)
    ap.add_argument("--wait", type=int, default=25)
    args = ap.parse_args()

    out_dir = DUMP_ROOT / args.mode / "artifact"
    out_dir.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("HEADLESS") == "1"

    net_log: list[str] = []

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
            if re.search(r"audio|video|music|rc_gen|rc/video|\\.mp4|\\.mp3|\\.flac|\\.m4a", url + ct, re.I):
                net_log.append(f"{resp.status} {ct} {url}")

        page.on("response", _on_response)
        try:
            await page.goto(args.url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[artifact] goto failed: {exc}", flush=True)
        await asyncio.sleep(6)
        await _dump(page, out_dir, "00_open")

        if args.mode == "music":
            play = page.locator('[class*="play-button-wrapper"]')
            if await play.count():
                try:
                    await play.last.click(force=True, timeout=8000)
                    print("[artifact] clicked play", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[artifact] play click failed: {exc}", flush=True)
        elif args.mode == "image":
            img = page.locator('[data-testid="message-block-container"] img[src^="http"]')
            if await img.count():
                try:
                    await img.last.click(force=True, timeout=8000)
                    print("[artifact] clicked image", flush=True)
                    await asyncio.sleep(3)
                    await _dump(page, out_dir, "10_preview")
                    btn = page.locator('button').filter(
                        has_text=re.compile("下载|保存|download", re.I)
                    )
                    if await btn.count():
                        try:
                            async with page.expect_download(timeout=8000) as dl:
                                await btn.first.click(force=True)
                            d = await dl.value
                            p = out_dir / "downloads" / (d.suggested_filename or "img")
                            await d.save_as(str(p))
                            print("[artifact] downloaded:", p, flush=True)
                        except Exception as exc:  # noqa: BLE001
                            print(f"[artifact] download failed: {exc}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[artifact] image click failed: {exc}", flush=True)
        else:  # video
            vid = page.locator('video')
            if await vid.count():
                try:
                    await vid.last.click(force=True, timeout=8000)
                    print("[artifact] clicked video", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[artifact] video click failed: {exc}", flush=True)

        for i in range(args.wait // 3):
            await asyncio.sleep(3)
            await _dump(page, out_dir, f"20_tick_{i:02d}")

        (out_dir / "net.log").write_text("\n".join(net_log), encoding="utf-8")
        print(f"[artifact] done; {len(net_log)} network hits -> net.log", flush=True)
        for line in net_log[-20:]:
            print("   net:", line[:220], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
