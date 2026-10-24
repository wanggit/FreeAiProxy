"""Doubao video-result probe #12 (calibration aid, not a test).

The rendered video is announced ("你的视频生成好了。") but the clip is not a
`<video>` inside the assistant bubble; this probe dumps every media-ish node
on the page (video/audio/img/iframe/canvas, `video` testids, video CDN urls)
so the real result DOM can be wired into `wait_videos`.

    uv run python poc/doubao_probe12.py --url "https://www.doubao.com/chat/<id>"

Dumps land in poc/dump/doubao/video/result/.
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

SCAN_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const out = {videos: [], audios: [], imgs: [], iframes: [], testids: [], links: []};
  for (const v of document.querySelectorAll('video')) {
    const card = v.closest('[data-testid], [class*="card"], [class*="Card"]');
    out.videos.push({
      src: (v.currentSrc || v.src || '').slice(0, 600),
      poster: (v.poster || '').slice(0, 300),
      cls: String(v.className || '').slice(0, 120),
      parentCls: String(v.parentElement && v.parentElement.className || '').slice(0, 120),
      cardCls: card ? String(card.className || '').slice(0, 120) : '',
      cardTestid: card ? (card.getAttribute('data-testid') || '') : '',
    });
  }
  for (const a of document.querySelectorAll('audio')) {
    out.audios.push({src: (a.currentSrc || a.src || '').slice(0, 600)});
  }
  for (const img of document.querySelectorAll('img')) {
    const src = img.currentSrc || img.src || '';
    if (/gen|seedance|video|doubao/i.test(src)) out.imgs.push({src: src.slice(0, 400)});
  }
  for (const f of document.querySelectorAll('iframe')) out.iframes.push({src: f.src});
  for (const el of document.querySelectorAll('[data-testid]')) {
    const id = el.getAttribute('data-testid') || '';
    if (/video|play|player|media|download/i.test(id)) {
      out.testids.push({id, text: clean(el.innerText).slice(0, 40)});
    }
  }
  for (const el of document.querySelectorAll('a[href], [class*="download" i]')) {
    const href = el.getAttribute('href') || '';
    if (/video|mp4|download/i.test(href + el.className)) {
      out.links.push({href: href.slice(0, 400), text: clean(el.innerText).slice(0, 40),
                      cls: String(el.className || '').slice(0, 100)});
    }
  }
  const last = document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]');
  const lm = last.length ? last[last.length - 1] : null;
  return {
    url: location.href,
    aiCount: last.length,
    lastHtml: lm ? (lm.outerHTML || '') : '',
    ...out,
  };
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    args = ap.parse_args()

    out_dir = DUMP_ROOT / "video" / "result"
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
            if re.search(r"video|mp4|media|seedance", url + " " + ct, re.I):
                net.append(f"{resp.status} {ct} {url}")

        page.on("response", _on_response)
        try:
            await page.goto(args.url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[result] goto failed: {exc}", flush=True)
        await asyncio.sleep(10)

        state = await page.evaluate(SCAN_JS)
        (out_dir / "00_page.html").write_text(await page.content(), encoding="utf-8")
        (out_dir / "00_last.html").write_text(state.pop("lastHtml"), encoding="utf-8")
        (out_dir / "00_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "net.log").write_text("\n".join(net), encoding="utf-8")
        print(f"[result] ai={state['aiCount']}", flush=True)
        for key in ("videos", "audios", "imgs", "iframes", "testids", "links"):
            for item in state.get(key, []):
                print(f"  {key}: {json.dumps(item, ensure_ascii=False)[:220]}", flush=True)
        print(f"[result] done; {len(net)} network hits", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
