"""Doubao skill calibration probe #3 (calibration aid, not a test).

Enters one of the doubao generation skills (图像生成 / 视频生成 / 音乐生成 /
chat), dumps the composer together with the skill's option controls (ratio,
style, duration, ...), and — with --prompt — types the prompt, sends it and
polls the answer area for generated media.

    uv run python poc/doubao_probe3.py image
    uv run python poc/doubao_probe3.py image --prompt "一只卡通猫" --send
    uv run python poc/doubao_probe3.py music --prompt "轻快的夏天" --send
    HEADLESS=1 uv run python poc/doubao_probe3.py image   # needs login state

Dumps land in poc/dump/doubao/<mode>/ .
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

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"
START_URL = "https://www.doubao.com/chat/"

SKILL_TESTIDS = {
    "image": "skill_bar_button_3",
    "video": "skill_bar_button_17",
    "music": "skill_bar_button_9",
}

NET_BODY_HINTS = re.compile(
    r'"(ratio|aspect|style|duration|resolution|quality|skill_type|config_key|'
    r'option_list|music_generation|image_generation|video_generation)"'
)


def _safe_name(url: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:90].strip("_")
    return name or "resp"


async def _dump_net(response, net_dir: Path) -> None:  # noqa: ANN001
    try:
        url = response.url
        ctype = (response.headers or {}).get("content-type", "")
        if "json" not in ctype and "text" not in ctype:
            return
        body = await response.body()
    except Exception:  # noqa: BLE001
        return
    if not body or len(body) > 600_000:
        return
    text = body.decode("utf-8", "replace")
    if not NET_BODY_HINTS.search(text):
        return
    (net_dir / f"{int(time.time() * 1000)}_{_safe_name(url)}.json").write_text(
        text, encoding="utf-8"
    )
    print(f"[net] {url[:160]}", flush=True)


COMPOSER_JS = """
() => {
  const root = document.querySelector('[data-testid="chat_input"]')
    || document.querySelector('[data-testid="chat_input_input"]')?.parentElement
    || document.body;
  const fmt = (el) => ({
    tag: el.tagName.toLowerCase(),
    testid: el.getAttribute('data-testid') || '',
    aria: el.getAttribute('aria-label') || '',
    title: el.getAttribute('title') || '',
    role: el.getAttribute('role') || '',
    cls: String(el.className || '').slice(0, 200),
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
    html: (el.outerHTML || '').replace(/\\s+/g, ' ').slice(0, 500),
  });
  const buttons = Array.from(document.querySelectorAll(
      'button, [role="button"], [role="menuitem"], [role="menuitemcheckbox"],'
      + '[role="menuitemradio"], [role="option"], [role="switch"], [role="tab"],'
      + '[role="radio"], [role="combobox"], [role="listbox"]'))
    .map(fmt);
  const editors = Array.from(document.querySelectorAll(
      'textarea, [contenteditable], [role="textbox"]')).map(fmt);
  const hits = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  const seen = new Set();
  while ((node = walker.nextNode())) {
    const t = (node.nodeValue || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 30) continue;
    if (/比例|风格|时长|清晰度|分辨率|画幅|写实|动漫|发送|更多|选项|设置|运镜|音乐|曲风|音色|歌词|上传|生成/.test(t)) {
      const el = node.parentElement;
      if (!el) continue;
      const key = t + '@' + String(el.className || '').slice(0, 60);
      if (seen.has(key)) continue;
      seen.add(key);
      hits.push(fmt(el));
    }
  }
  const media = Array.from(document.querySelectorAll('img,video,audio,source'))
    .map((el) => ({
      tag: el.tagName.toLowerCase(),
      src: (el.currentSrc || el.src || '').slice(0, 400),
      cls: String(el.className || '').slice(0, 160),
      alt: el.getAttribute('alt') || '',
      html: (el.outerHTML || '').replace(/\\s+/g, ' ').slice(0, 400),
    })).slice(0, 80);
  return {
    url: location.href,
    composerHtml: (root.outerHTML || '').replace(/\\s+/g, ' ').slice(0, 20000),
    buttons,
    editors,
    hits,
    media,
    bodyText: (document.body.innerText || '').slice(0, 8000),
  };
}
"""


async def dump(page, out_dir: Path, tag: str) -> dict:  # noqa: ANN001
    info = await page.evaluate(COMPOSER_JS)
    (out_dir / f"{tag}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"{tag}.html").write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(out_dir / f"{tag}.png"), full_page=False)
    print(
        f"[{tag}] url={info['url']} buttons={len(info['buttons'])} "
        f"editors={len(info['editors'])} hits={len(info['hits'])} "
        f"media={len(info['media'])}",
        flush=True,
    )
    return info


async def enter_skill(page, mode: str) -> None:
    if mode == "chat":
        return
    testid = SKILL_TESTIDS.get(mode)
    if testid:
        locator = page.locator(f'[data-testid="{testid}"]')
        if await locator.count():
            await locator.first.click()
            await asyncio.sleep(2)
            return
    # Fall back to the "更多" skill entry and pick by label.
    more = page.locator('[data-testid="skill_bar_button_more"]')
    if await more.count():
        await more.first.click()
        await asyncio.sleep(1.5)
    label = {"image": "图像生成", "video": "视频生成", "music": "音乐生成"}[mode]
    item = page.get_by_text(label, exact=True)
    if await item.count():
        await item.first.click()
        await asyncio.sleep(2)


async def type_prompt(page, prompt: str) -> None:
    editor = page.locator('[data-testid="chat_input_input"] [contenteditable]')
    if not await editor.count():
        editor = page.locator("[contenteditable]").last
    await editor.click()
    await page.keyboard.type(prompt, delay=12)
    await asyncio.sleep(1)


async def send(page) -> None:
    for sel in (
        '[data-testid="chat_input"] button[aria-label*="发送"]',
        'button[aria-label*="发送"]',
        '[data-testid="send_button"]',
    ):
        loc = page.locator(sel)
        if await loc.count():
            await loc.first.click()
            return
    await page.keyboard.press("Enter")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["image", "video", "music", "chat"])
    ap.add_argument("--prompt", default="一只可爱的卡通猫咪头像")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--poll", type=int, default=150)
    args = ap.parse_args()

    out_dir = DUMP_ROOT / args.mode
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "net").mkdir(exist_ok=True)
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
        page.on(
            "response",
            lambda r: asyncio.ensure_future(_dump_net(r, out_dir / "net")),
        )
        try:
            await page.goto(START_URL, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[probe3] goto failed: {exc}", flush=True)
        await asyncio.sleep(4)
        await dump(page, out_dir, "01_home")

        await enter_skill(page, args.mode)
        await asyncio.sleep(2)
        await dump(page, out_dir, "02_skill")

        if not args.send:
            return

        await type_prompt(page, args.prompt)
        await asyncio.sleep(1)
        await dump(page, out_dir, "03_typed")

        await send(page)
        await asyncio.sleep(3)
        await dump(page, out_dir, "04_sent")

        for i in range(args.poll // 3):
            await asyncio.sleep(3)
            info = await dump(page, out_dir, f"10_poll_{i:02d}")
            media = [m for m in info["media"] if m["src"] and not m["src"].startswith("data:")]
            if (
                media
                and "上传" not in info["bodyText"][-400:]
                and args.mode in ("image", "video", "music")
            ):
                print("[probe3] media candidate captured", flush=True)
                await asyncio.sleep(6)
                await dump(page, out_dir, "20_final")
                return
        print("[probe3] poll window exhausted", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
