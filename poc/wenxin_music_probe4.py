"""Wenxin 音乐生成 option popovers probe (calibration aid, part 4).

Enters 音乐生成, opens 选曲风 / 选音色 / AI写歌词 (each a .ci-tooltip trigger)
and dumps each popover's option list; screenshots each state.

    uv run python poc/wenxin_music_probe4.py
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

CLICK_CAPSULE_JS = """
(label) => {
  const spans = Array.from(document.querySelectorAll('.capsule-text'));
  const span = spans.find(s => (s.innerText||'').replace(/\\s+/g,' ').trim() === label);
  if (!span) return false;
  (span.closest('.ci-panel') || span).click();
  return true;
}
"""

CLICK_TOOLTIP_JS = """
(label) => {
  const spans = Array.from(document.querySelectorAll('.ci-tooltip'));
  const span = spans.find(s => (s.innerText||'').replace(/\\s+/g,' ').trim() === label);
  if (!span) return false;
  span.click();
  ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t => {
    const E = t.startsWith('pointer') ? (window.PointerEvent || MouseEvent) : MouseEvent;
    span.dispatchEvent(new E(t, {bubbles: true, cancelable: true}));
  });
  return true;
}
"""

POPUP_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  // Find the topmost portal-ish container that appeared after the click:
  // any body-level subtree that contains option-like leaves and overlaps none of the composer.
  const candidates = Array.from(document.querySelectorAll('body > div, body > section'))
    .filter(el => visible(el) && !el.querySelector('textarea, .ci-editor'))
    .map(el => ({el, n: el.querySelectorAll('*').length}));
  const out = [];
  for (const {el} of candidates) {
    const leaves = Array.from(el.querySelectorAll('*')).filter(x =>
      visible(x) && x.children.length === 0 && (x.innerText || '').trim() && (x.innerText || '').trim().length < 24);
    if (leaves.length >= 2) {
      out.push({
        cls: String(el.className || '').slice(0, 140),
        leaves: leaves.map(x => ({
          text: (x.innerText || '').trim(),
          cls: String(x.className || '').slice(0, 100),
          parentCls: String(x.parentElement?.className || '').slice(0, 100),
        })).slice(0, 60),
      });
    }
  }
  return {url: location.href, popups: out.slice(0, 4)};
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
        await page.locator(".more-dropdown-trigger").first.click(timeout=8000)
        await asyncio.sleep(1.0)
        ok = await page.evaluate(CLICK_CAPSULE_JS, "音乐生成")
        print(f"[probe] entered music mode: {ok}", flush=True)
        await asyncio.sleep(1.5)

        for label, key in (("选曲风", "style"), ("选音色", "timbre"), ("AI写歌词", "lyrics")):
            clicked = await page.evaluate(CLICK_TOOLTIP_JS, label)
            print(f"[probe] open {label} -> {clicked}", flush=True)
            await asyncio.sleep(1.2)
            dump = await page.evaluate(POPUP_JS)
            _save(f"m4_{key}", dump)
            await page.screenshot(path=str(DUMP_DIR / f"m4_{key}.png"))
            for p in dump["popups"]:
                texts = [x["text"] for x in p["leaves"]]
                print(f"  {label} popup[{p['cls'][:40]}]: {texts[:40]}", flush=True)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.8)


if __name__ == "__main__":
    asyncio.run(main())
