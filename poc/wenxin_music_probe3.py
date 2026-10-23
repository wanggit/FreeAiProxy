"""Wenxin 音乐生成 mode + options probe (calibration aid, part 3).

Clicks 更多 -> 音乐生成, dumps the composer, then opens each of the
曲风 / 音色 / 歌词 controls and dumps the popover contents.

    uv run python poc/wenxin_music_probe3.py
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

DUMP_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const brief = (el) => ({
    tag: el.tagName.toLowerCase(),
    cls: String(typeof el.className === 'string' ? el.className : (el.className?.baseVal ?? '')).slice(0, 160),
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    rect: (r => [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)])(el.getBoundingClientRect()),
  });
  const input = document.querySelector('div.ci-editor, textarea');
  const box = input ? input.closest('[class*="chat-input"]') || input.parentElement : null;
  const zone = box ? Array.from(box.querySelectorAll('*')).filter(visible).map(brief).slice(0, 150) : [];
  return {
    url: location.href,
    ph: input ? (input.getAttribute('data-placeholder') || input.getAttribute('placeholder') || '') : '',
    editorCls: input ? String(input.className || '').slice(0, 160) : '',
    composerZone: zone,
    capsules: Array.from(document.querySelectorAll('.capsule-text')).filter(visible).map(brief),
  };
}
"""

POPUP_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const brief = (el) => ({
    tag: el.tagName.toLowerCase(),
    cls: String(el.className || '').slice(0, 140),
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 60),
    rect: (r => [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)])(el.getBoundingClientRect()),
  });
  const popups = Array.from(document.querySelectorAll('body > *'))
    .filter(el => visible(el) && /dropdown|menu|popover|popup|panel|select|drawer|modal/i.test(String(el.className||'')));
  return popups.map(el => ({cls: String(el.className||'').slice(0,140),
    items: Array.from(el.querySelectorAll('*')).filter(x => visible(x) && (x.innerText||'').trim() && x.children.length === 0).map(brief).slice(0, 80)})).slice(0, 6);
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

CLICK_EXACT_JS = """
(label) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const els = Array.from(document.querySelectorAll('span,div,button,[role="button"]'));
  const hits = els.filter(el => visible(el) && (el.innerText || '').replace(/\\s+/g, ' ').trim() === label);
  if (!hits.length) return {clicked: false, count: 0};
  const inner = hits.reduce((a, b) => (a.textContent||'').length <= (b.textContent||'').length ? a : b);
  ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t =>
    inner.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
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

        await page.locator(".more-dropdown-trigger").first.click(timeout=8000)
        await asyncio.sleep(1.0)
        ok = await page.evaluate(CLICK_CAPSULE_JS, "音乐生成")
        print(f"[probe] capsule 音乐生成 clicked={ok}", flush=True)
        await asyncio.sleep(2.0)
        dump = await page.evaluate(DUMP_JS)
        _save("m3_mode", dump)
        await page.screenshot(path=str(DUMP_DIR / "m3_mode.png"))
        print(f"[probe] url={dump['url']} ph={dump['ph']}", flush=True)
        for z in dump["composerZone"]:
            if z["text"] and len(z["text"]) < 40:
                print("  zone:", z["tag"], "|", z["cls"][:50], "|", z["text"], flush=True)

        for needle in ("曲风", "音色", "歌词"):
            info = await page.evaluate(CLICK_EXACT_JS, needle)
            print(f"[probe] open {needle} -> {info}", flush=True)
            await asyncio.sleep(1.2)
            pop = await page.evaluate(POPUP_JS)
            _save(f"m3_pop_{needle}", {"click": info, "popups": pop})
            await page.screenshot(path=str(DUMP_DIR / f"m3_pop_{needle}.png"))
            n = sum(len(p["items"]) for p in pop)
            print(f"[probe] {needle}: popups={len(pop)} leafItems={n}", flush=True)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.6)


if __name__ == "__main__":
    asyncio.run(main())
