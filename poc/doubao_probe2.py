"""Doubao composer / skill-entry probe #2 (calibration aid, not a test).

Loads doubao.com/chat/ (HEADLESS=1 for a static pass) and dumps the
*client-rendered* composer geometry: every textarea / contenteditable /
role=textbox / iframe, every data-testid in the document (not only visible
ones), plus elements whose class or text hints at the input box, the send
button and the skill (技能) entry points.

    HEADLESS=1 uv run python poc/doubao_probe2.py
    uv run python poc/doubao_probe2.py            # headed watch loop
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"
START_URL = "https://www.doubao.com/chat/"
INTERVAL_S = 6
MAX_S = int(os.environ.get("MAX_S", str(20 * 60)))

SCAN_JS = """
() => {
  const seen = new Set();
  const walk = (root, depth) => {
    if (depth > 40) return;
    const els = root.querySelectorAll('*');
    for (const el of els) {
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
    }
  };
  walk(document, 0);
  const html = (el, n) => (el.outerHTML || '').replace(/\\s+/g, ' ').slice(0, n || 400);
  const pick = (sel) => Array.from(document.querySelectorAll(sel)).map((el) => ({
    tag: el.tagName.toLowerCase(),
    cls: String(el.className && el.className.baseVal !== undefined
      ? el.className.baseVal : el.className || '').slice(0, 240),
    testid: el.getAttribute('data-testid') || '',
    aria: el.getAttribute('aria-label') || '',
    ph: el.getAttribute('placeholder') || '',
    ce: el.getAttribute('contenteditable') || '',
    role: el.getAttribute('role') || '',
    html: html(el, 360),
  }));
  const testids = Array.from(document.querySelectorAll('[data-testid]'))
    .map((el) => el.getAttribute('data-testid'));
  const frames = Array.from(document.querySelectorAll('iframe')).map((f) => ({
    src: (f.src || '').slice(0, 200), cls: String(f.className || '').slice(0, 160),
    id: f.id || '',
  }));
  const textHits = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const t = (node.nodeValue || '').trim();
    if (!t || t.length > 40) continue;
    if (/发送|输入|技能|图像|视频|音乐|更多|开启|登录|比例|风格|时长|清晰度/.test(t)) {
      const el = node.parentElement;
      if (!el) continue;
      const key = t + '|' + (el.tagName + '.' + String(el.className || '')).slice(0, 80);
      if (seen.has(key)) continue;
      seen.add(key);
      textHits.push({
        text: t.slice(0, 60),
        tag: el.tagName.toLowerCase(),
        cls: String(el.className || '').slice(0, 200),
        testid: el.getAttribute('data-testid') || '',
        html: html(el, 300),
      });
    }
  }
  return {
    url: location.href,
    editors: pick('textarea, [contenteditable], [role="textbox"], input[type="text"]'),
    buttons: pick('button, [role="button"], [role="menuitem"], [role="menuitemcheckbox"], [role="switch"], [role="tab"]'),
    frames,
    testids,
    textHits,
    bodyText: (document.body.innerText || '').slice(0, 6000),
  };
}
"""


async def dump(page, tag: str) -> None:  # noqa: ANN001
    info = await page.evaluate(SCAN_JS)
    (DUMP_DIR / f"scan_{tag}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DUMP_DIR / f"scan_{tag}.html").write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(DUMP_DIR / f"scan_{tag}.png"), full_page=False)
    print(
        f"[{tag}] url={info['url']} editors={len(info['editors'])} "
        f"buttons={len(info['buttons'])} testids={len(info['testids'])} "
        f"hits={len(info['textHits'])}",
        flush=True,
    )


async def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
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
            print(f"[probe2] initial goto failed: {exc}", flush=True)
        await asyncio.sleep(5)
        await dump(page, "01")
        await asyncio.sleep(6)
        await dump(page, "02")
        if headless:
            print("[probe2] headless pass done", flush=True)
            return
        t0 = time.monotonic()
        n = 2
        while time.monotonic() - t0 < MAX_S:
            await asyncio.sleep(INTERVAL_S)
            n += 1
            try:
                await dump(page, f"{n:02d}")
            except Exception as exc:  # noqa: BLE001
                print(f"[probe2] #{n} dump failed: {exc}", flush=True)
        print("[probe2] watch loop finished", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
