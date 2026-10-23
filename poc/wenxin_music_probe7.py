"""Wenxin 音乐生成 result polling probe (calibration aid, part 7).

Opens the most recent conversation (the 自定义歌词 generation started by
probe6) and polls until the ai-music-player card exposes an <audio> src,
then dumps the player HTML, download controls and screenshots.

    uv run python poc/wenxin_music_probe7.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "wenxin"
PROFILE_DIR = ROOT / "data" / "profiles" / "wenxin"
START_URL = "https://wenxin.baidu.com/"

MUSIC_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const players = Array.from(document.querySelectorAll('.ai-music-player')).filter(visible);
  const audio = [];
  for (const a of document.querySelectorAll('audio, source')) {
    const src = a.currentSrc || a.src || '';
    if (src) audio.push({tag: a.tagName.toLowerCase(), cls: String(a.className||'').slice(0,80), src: src.slice(0, 500)});
  }
  const buttons = [];
  for (const el of document.querySelectorAll('button,[role="button"],i,span,div')) {
    const blob = `${el.getAttribute('aria-label')||''} ${el.getAttribute('title')||''} ${String(el.className||'')}`;
    if (/download|下载|导出/.test(blob) && visible(el)) {
      const r = el.getBoundingClientRect();
      buttons.push({tag: el.tagName.toLowerCase(), cls: String(el.className||'').slice(0,120),
        text:(el.innerText||'').trim().slice(0,30), aria: el.getAttribute('aria-label')||'',
        title: el.getAttribute('title')||'', rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]});
    }
  }
  const as = Array.from(document.querySelectorAll('[class*="conversation-flow-answer-container"]'));
  const last = as.length ? as[as.length - 1] : null;
  return {
    url: location.href,
    aCount: as.length,
    players: players.map(p => ({html: p.outerHTML.slice(0, 2500), loading: !!p.querySelector('[class*="loading"]')})),
    audio, buttons: buttons.slice(0, 20),
    lastText: last ? (last.innerText||'').replace(/\\s+/g,' ').trim().slice(0, 300) : '',
  };
}
"""


def _save(name: str, data) -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def main() -> None:
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
        locale="zh-CN",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.locator("div.ci-editor, textarea.ci-textarea").first.wait_for(
            state="visible", timeout=30000
        )
        await asyncio.sleep(1.5)
        hist = page.locator(".history-item, [class*='history-item']").first
        try:
            await hist.click(timeout=6000)
            print("[probe] opened latest history item", flush=True)
        except Exception as exc:
            print(f"[probe] history click failed: {exc}", flush=True)
        await asyncio.sleep(3.0)

        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < 420:
            await asyncio.sleep(10)
            n += 1
            info = await page.evaluate(MUSIC_JS)
            loading = any(p["loading"] for p in info["players"])
            print(
                f"[probe] t={int(time.monotonic()-t0)}s url={info['url'][-40:]} players={len(info['players'])} "
                f"loading={loading} audio={len(info['audio'])} btns={len(info['buttons'])} "
                f"text={info['lastText'][:50]!r}",
                flush=True,
            )
            _save(f"m7_s{n:02d}", info)
            if info["audio"]:
                await page.screenshot(path=str(DUMP_DIR / "m7_done.png"))
                _save("m7_done", info)
                for a in info["audio"]:
                    print("  AUDIO:", a["src"][:200], flush=True)
                for b in info["buttons"]:
                    print("  BTN:", b["tag"], b["cls"][:60], b["text"], b["aria"], b["title"], flush=True)
                return
        print("[probe] timed out waiting for audio", flush=True)
        await page.screenshot(path=str(DUMP_DIR / "m7_end.png"))


if __name__ == "__main__":
    asyncio.run(main())
