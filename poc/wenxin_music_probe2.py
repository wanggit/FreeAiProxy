"""Wenxin 更多-dropdown probe (calibration aid, part 2).

Tries real mouse click and hover on .more-dropdown-trigger, then dumps every
visible element whose text mentions 音乐 plus the dropdown portal HTML.

    uv run python poc/wenxin_music_probe2.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "wenxin"
PROFILE_DIR = ROOT / "data" / "profiles" / "wenxin"
START_URL = "https://wenxin.baidu.com/"

SCAN_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const hits = [];
  for (const el of document.querySelectorAll('*')) {
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('').replace(/\\s+/g,' ').trim();
    if (own && /音乐|视频生成|数字人|深入研究|AI搜索|翻译|更短|智能体/.test(own) && visible(el)) {
      const r = el.getBoundingClientRect();
      hits.push({tag: el.tagName.toLowerCase(), cls: String(el.className||'').slice(0,140),
                 text: own.slice(0,40), rect: [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]});
    }
  }
  const menus = Array.from(document.querySelectorAll('[class*="dropdown"],[class*="menu"],[class*="popover"],[class*="popup"]'))
    .filter(visible).map(el => ({cls: String(el.className||'').slice(0,140),
      html: el.outerHTML.slice(0, 3000)}));
  return {url: location.href, hits: hits.slice(0,40), menus: menus.slice(0,8)};
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

        trigger = page.locator(".more-dropdown-trigger").first
        print(f"[probe] trigger count={await page.locator('.more-dropdown-trigger').count()}", flush=True)

        # attempt 1: hover
        try:
            await trigger.hover()
            await asyncio.sleep(1.0)
            scan = await page.evaluate(SCAN_JS)
            print(f"[probe] after hover: hits={len(scan['hits'])}", flush=True)
            if scan["hits"]:
                _save("dd_hover", scan)
                await page.screenshot(path=str(DUMP_DIR / "dd_hover.png"))
        except Exception as exc:
            print(f"[probe] hover failed: {exc}", flush=True)

        # attempt 2: real click
        try:
            await trigger.click(timeout=8000)
        except Exception as exc:
            print(f"[probe] real click failed: {exc}", flush=True)
            await page.evaluate(
                """() => { const el = document.querySelector('.more-dropdown-trigger');
                    if (el) { ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t =>
                      el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window}))); } }"""
            )
        await asyncio.sleep(1.2)
        scan = await page.evaluate(SCAN_JS)
        print(f"[probe] after click: hits={len(scan['hits'])} menus={len(scan['menus'])}", flush=True)
        _save("dd_click", scan)
        await page.screenshot(path=str(DUMP_DIR / "dd_click.png"))
        for h in scan["hits"]:
            print("  hit:", h["tag"], h["text"], h["cls"][:60], h["rect"], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
