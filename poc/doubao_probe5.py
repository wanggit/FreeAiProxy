"""Doubao generation probe #5 (calibration aid, not a test).

Runs one full generation flow end to end — enters the skill, fills the
prompt (and the music theme field), sends, and polls the message list until
the answer settles. Every poll dumps the message containers, the send-button
state and the media inside the answer, so completion signals and result-URL
shapes can be read off the dumps.

    uv run python poc/doubao_probe5.py chat   --prompt "用一句话介绍你自己"
    uv run python poc/doubao_probe5.py image  --prompt "一只卡通熊猫" --poll 180
    uv run python poc/doubao_probe5.py music  --prompt "夏天的海边"   --poll 300
    uv run python poc/doubao_probe5.py video  --prompt "海浪拍打沙滩"  --poll 600

Dumps land in poc/dump/doubao/<mode>/gen/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

sys.path.insert(0, str(ROOT / "poc"))  # noqa: E402
from doubao_probe3 import START_URL, enter_skill, type_prompt  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"

GEN_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const msgs = Array.from(document.querySelectorAll(
      '[data-testid="message-block-container"]')).map((el, i) => ({
    i,
    role: el.getAttribute('data-message-role') || el.getAttribute('data-role') || '',
    cls: String(el.className || '').slice(0, 160),
    testid: el.getAttribute('data-testid') || '',
    text: clean(el.innerText).slice(0, 900),
    media: Array.from(el.querySelectorAll('img,video,audio,source')).map((m) => ({
      tag: m.tagName.toLowerCase(),
      src: (m.currentSrc || m.src || '').slice(0, 500),
      cls: String(m.className || '').slice(0, 140),
      poster: m.getAttribute('poster') || '',
    })).slice(0, 30),
    html: clean(el.outerHTML).slice(0, 4000),
  }));
  const sb = document.querySelector('[data-testid="chat_input_send_button"]');
  const editors = Array.from(document.querySelectorAll('[contenteditable]'))
    .map((el) => clean(el.innerText).slice(0, 300));
  return {
    url: location.href,
    messages: msgs,
    sendBtn: sb ? clean(sb.outerHTML).slice(0, 700) : '',
    editors,
    bodyText: (document.body.innerText || '').slice(0, 9000),
  };
}
"""

GEN_BODY_HINT = re.compile(r"正在生成|生成中|排队中|请稍候|正在作曲|正在演唱|创作中|加载中|loading", re.I)


async def dump(page, out_dir: Path, tag: str) -> dict:  # noqa: ANN001
    info = await page.evaluate(GEN_JS)
    (out_dir / f"{tag}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if tag.endswith("00") or tag.startswith("final") or tag.startswith("20"):
        (out_dir / f"{tag}.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(out_dir / f"{tag}.png"), full_page=False)
    print(
        f"[{tag}] url={info['url']} msgs={len(info['messages'])} "
        f"send={info['sendBtn'][:60]!r}",
        flush=True,
    )
    return info


def _settled(info: dict, mode: str) -> bool:
    if not info["messages"]:
        return False
    last = info["messages"][-1]
    if last["role"] == "user":
        return False
    if GEN_BODY_HINT.search(last["text"] or ""):
        return False
    media = [m for m in last["media"] if m["src"] and not m["src"].startswith("data:")]
    if mode in ("image", "music", "video"):
        need = {"image": "img", "music": "audio", "video": "video"}[mode]
        return any(m["tag"] == need for m in media) and len(last["text"]) > 0
    return len(last["text"]) > 0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["chat", "image", "music", "video"])
    ap.add_argument("--prompt", default="一只戴着红色围巾的卡通小熊猫")
    ap.add_argument("--poll", type=int, default=180)
    ap.add_argument("--hover", action="store_true", help="hover the first result media")
    args = ap.parse_args()

    out_dir = DUMP_ROOT / args.mode / "gen"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "downloads").mkdir(exist_ok=True)
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
            print(f"[gen] goto failed: {exc}", flush=True)
        await asyncio.sleep(4)
        await enter_skill(page, args.mode)
        await asyncio.sleep(2)

        if args.mode == "music":
            theme = page.locator('[data-testid="chat_input_input"] input[data-music-theme]')
            if await theme.count():
                await theme.first.fill(args.prompt)
                await asyncio.sleep(0.5)
        else:
            await type_prompt(page, args.prompt)
        await asyncio.sleep(1)
        await dump(page, out_dir, "00_typed")

        send = page.locator('[data-testid="chat_input_send_button"]')
        if await send.count():
            await send.first.click()
        else:
            await page.keyboard.press("Enter")
        await asyncio.sleep(4)
        await dump(page, out_dir, "01_sent")

        t0 = time.monotonic()
        i = 0
        last_text = ""
        stable = 0
        while time.monotonic() - t0 < args.poll:
            await asyncio.sleep(4)
            i += 1
            info = await dump(page, out_dir, f"10_poll_{i:02d}")
            if _settled(info, args.mode):
                text = info["messages"][-1]["text"]
                stable = stable + 1 if text == last_text else 1
                last_text = text
                if stable >= 3:
                    break
        final = await dump(page, out_dir, "20_final")

        if args.hover and final["messages"]:
            media = [
                m
                for m in final["messages"][-1]["media"]
                if m["src"] and not m["src"].startswith("data:")
            ]
            if media:
                loc = page.locator(
                    '[data-testid="message-block-container"] >> img >> visible=true'
                )
                try:
                    await loc.last.hover()
                    await asyncio.sleep(1.5)
                    await dump(page, out_dir, "21_hover")
                except Exception as exc:  # noqa: BLE001
                    print(f"[gen] hover failed: {exc}", flush=True)
        print("[gen] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
