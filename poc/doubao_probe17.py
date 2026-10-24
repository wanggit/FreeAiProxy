"""Doubao music-recovery probe #17 (calibration aid, not a test).

Replicates the driver's `_recover_music_url` against the conversation the
gateway's music E2E just produced, logging every network response so the
failed `get_video` capture can be explained.

    uv run python poc/doubao_probe17.py [--url https://www.doubao.com/chat/<id>]

Dumps land in poc/dump/doubao/music/recover/.
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

from freeaiproxy.engine import doubao_selectors as S  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"

STATE_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const card = document.querySelector('[data-testid="music-generation-card-content"]');
  const plays = Array.from(document.querySelectorAll('[class*="play-button-wrapper"]'));
  return {
    url: location.href,
    hasCard: !!card,
    finished: !!card && card.getAttribute('data-finished') === 'true',
    cardText: card ? clean(card.innerText).slice(0, 200) : '',
    playCount: plays.length,
    playHtml: plays.length ? (plays[plays.length - 1].outerHTML || '').slice(0, 800) : '',
    audio: Array.from(document.querySelectorAll('audio')).map((a) => a.currentSrc || a.src || ''),
  };
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://www.doubao.com/chat/")
    ap.add_argument("--pick", default="咖啡")
    ap.add_argument("--wait", type=int, default=240)
    args = ap.parse_args()

    out_dir = DUMP_ROOT / "music" / "recover"
    out_dir.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("HEADLESS") == "1"
    hits: list[str] = []

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
            if "/alice/" in resp.url or "music" in resp.url or ".mp4" in resp.url:
                hits.append(f"{resp.status} {resp.url}")
                print(f"[recover] net {resp.status} {resp.url[:140]}", flush=True)

        page.on("response", lambda r: asyncio.ensure_future(_capture(r)))

        if "/chat/" not in args.url:
            await page.goto("https://www.doubao.com/chat/", wait_until="domcontentloaded")
            await asyncio.sleep(6)
            item = page.locator('[data-testid="conversation-list-v2-item"]') \
                .filter(has_text=args.pick)
            if not await item.count():
                texts = await page.locator('[data-testid="conversation-list-v2-item"]') \
                    .all_inner_texts()
                print("[recover] no item matched; recent:", texts[:8], flush=True)
                return
            await item.first.click(force=True)
            await asyncio.sleep(5)
        else:
            await page.goto(args.url, wait_until="domcontentloaded")
            await asyncio.sleep(6)

        state = await page.evaluate(STATE_JS)
        print("[recover] state:", json.dumps(state, ensure_ascii=False)[:400], flush=True)

        if not state["hasCard"]:
            deadline = args.wait
            t = 0
            while t < deadline and not state["hasCard"]:
                await asyncio.sleep(5)
                t += 5
                state = await page.evaluate(STATE_JS)
                print(f"[recover] wait {t}s hasCard={state['hasCard']} "
                      f"finished={state['finished']}", flush=True)
        if not state["hasCard"]:
            print("[recover] no music card", flush=True)
            return

        # wait for finished if needed
        t = 0
        while not state["finished"] and t < args.wait:
            await asyncio.sleep(5)
            t += 5
            state = await page.evaluate(STATE_JS)
        print(f"[recover] finished={state['finished']} after {t}s", flush=True)
        (out_dir / "00_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        play = page.locator(S.PLAY_BUTTON)
        count = await play.count()
        print(f"[recover] play buttons: {count}", flush=True)
        if not count:
            (out_dir / "no_play.html").write_text(await page.content(), encoding="utf-8")
            return

        try:
            async with page.expect_response(
                lambda r: S.MUSIC_MEDIA_API in r.url, timeout=45_000
            ) as pending:
                await play.last.click(force=True, timeout=10_000)
                print("[recover] clicked play.last", flush=True)
            response = await pending.value
            body = await response.json()
            (out_dir / "01_api.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=2)[:20000], encoding="utf-8"
            )
            print("[recover] got get_video:", str(body)[:200], flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[recover] capture failed: {exc}", flush=True)
            (out_dir / "01_after.html").write_text(await page.content(), encoding="utf-8")

        await asyncio.sleep(8)
        state = await page.evaluate(STATE_JS)
        print("[recover] after play:", json.dumps(state, ensure_ascii=False)[:300], flush=True)
        (out_dir / "net.log").write_text("\n".join(hits), encoding="utf-8")
        print(f"[recover] done; {len(hits)} hits", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
