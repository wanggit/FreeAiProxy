"""Wenxin 音乐生成 composer probe (calibration aid).

Opens the home page, expands the 更多 capsule menu, enters 音乐生成 and dumps
the composer area: every visible control near the editor (labels, chips,
buttons), placeholders and the option popovers for 曲风 / 音色 / 歌词.
Writes JSON + screenshots to poc/dump/wenxin/.

    uv run python poc/wenxin_music_probe1.py
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

VIS_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const brief = (el) => ({
    tag: el.tagName.toLowerCase(),
    cls: String(typeof el.className === 'string' ? el.className : (el.className?.baseVal ?? '')).slice(0, 160),
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 60),
    aria: el.getAttribute('aria-label') || '',
    rect: (r => [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)])(el.getBoundingClientRect()),
  });
  return {
    url: location.href,
    capsules: Array.from(document.querySelectorAll('.capsule-text')).filter(visible).map(brief),
    controls: Array.from(document.querySelectorAll('button,[role="button"],.ci-panel,[class*="capsule"],[class*="select"],[class*="option"],[class*="tag"],[class*="trigger"]'))
      .filter(visible).map(brief).slice(0, 120),
    editors: Array.from(document.querySelectorAll('div.ci-editor, textarea')).filter(visible)
      .map(el => ({...brief(el), ph: el.getAttribute('data-placeholder') || el.getAttribute('placeholder') || ''})),
  };
}
"""

CLICK_CAPSULE_JS = """
(label) => {
  const spans = Array.from(document.querySelectorAll('.capsule-text'));
  const span = spans.find(s => (s.innerText||'').replace(/\\s+/g,' ').trim() === label);
  if (!span) return false;
  (span.closest('.ci-panel') || span).click();
  return true;
}
"""

CLICK_TEXT_JS = """
(label) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const els = Array.from(document.querySelectorAll('div,span,li,a,button,[role="menuitem"],[role="option"]'));
  const hits = els.filter(el => visible(el) && (el.innerText || '').replace(/\\s+/g, ' ').trim() === label);
  if (!hits.length) return {clicked: false, count: 0};
  const inner = hits.reduce((a, b) => (a.textContent||'').length <= (b.textContent||'').length ? a : b);
  inner.click();
  return {clicked: true, count: hits.length, tag: inner.tagName.toLowerCase(), cls: String(inner.className||'').slice(0,160)};
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
        _save("m_home_capsules", await page.evaluate(VIS_JS))
        print("[probe] home capsules dumped", flush=True)

        # 1. expand 更多 if present.
        more = await page.evaluate(CLICK_TEXT_JS, "更多")
        print(f"[probe] click 更多 -> {more}", flush=True)
        if not more["clicked"]:
            more = await page.evaluate(CLICK_CAPSULE_JS, "更多")
            print(f"[probe] capsule 更多 -> {more}", flush=True)
        await asyncio.sleep(1.2)
        _save("m_after_more", await page.evaluate(VIS_JS))
        await page.screenshot(path=str(DUMP_DIR / "m_after_more.png"))

        # 2. enter 音乐生成.
        ent = await page.evaluate(CLICK_TEXT_JS, "音乐生成")
        print(f"[probe] click 音乐生成 -> {ent}", flush=True)
        await asyncio.sleep(2.0)
        _save("m_mode_state", await page.evaluate(VIS_JS))
        await page.screenshot(path=str(DUMP_DIR / "m_mode_state.png"))

        # 3. dump the three option controls: click each of 曲风/音色/歌词 trigger and dump popover.
        for needle in ("曲风", "音色", "歌词"):
            info = await page.evaluate(CLICK_TEXT_JS, needle)
            print(f"[probe] click {needle} -> {info}", flush=True)
            await asyncio.sleep(1.0)
            dump = await page.evaluate(VIS_JS)
            dump["clicked"] = info
            dump["bodyText"] = (await page.evaluate("() => document.body.innerText || ''"))[:3000]
            _save(f"m_opt_{needle}", dump)
            await page.screenshot(path=str(DUMP_DIR / f"m_opt_{needle}.png"))
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

        # 4. keep the browser open briefly so screenshots settle.
        await asyncio.sleep(0.5)
        print("[probe] done", flush=True)
        time.sleep(0)


if __name__ == "__main__":
    asyncio.run(main())
