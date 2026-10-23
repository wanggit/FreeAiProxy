"""Wenxin per-mode composer probe (calibration aid).

For each agent capsule (帮我写作 / 图片生成 / PPT生成), click it from home and
dump the resulting composer: editables, clickables, body text, screenshot.
Writes to poc/dump/wenxin/.

    uv run python poc/wenxin_probe5.py
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
MODES = [("writing", "帮我写作"), ("image", "图片生成"), ("ppt", "PPT生成")]

INV_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const txt = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    text: (txt(el) || el.getAttribute('aria-label') || '').slice(0, 80),
    cls: String(el.className || '').slice(0, 180),
    role: el.getAttribute('role') || '',
    aria: el.getAttribute('aria-label') || '',
    ph: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '',
    ce: el.getAttribute('contenteditable') || '',
    type: el.getAttribute('type') || '',
  });
  const editables = Array.from(document.querySelectorAll(
      'textarea,input,[contenteditable="true"],[contenteditable=""],[role="textbox"]'))
    .filter(visible).map(desc).slice(0, 30);
  const clickables = Array.from(document.querySelectorAll(
      'button,[role="button"],[role="menuitem"],[role="tab"],.ci-submit-button,.capsule-text,.ci-panel'))
    .filter(visible).map(desc).slice(0, 120);
  return {url: location.href, bodyText: (document.body.innerText||'').slice(0,2500), editables, clickables};
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


def _save(name: str, data) -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[dump] {name}", flush=True)


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
        for slug, label in MODES:
            await page.goto(START_URL, wait_until="domcontentloaded")
            await page.locator("textarea.ci-textarea").first.wait_for(state="visible", timeout=30000)
            await asyncio.sleep(1.2)
            ok = await page.evaluate(CLICK_CAPSULE_JS, label)
            await asyncio.sleep(2.0)
            info = await page.evaluate(INV_JS)
            _save(f"m_{slug}", info)
            await page.screenshot(path=str(DUMP_DIR / f"m_{slug}.png"))
            print(f"[probe] {label}: clicked={ok} editables={len(info['editables'])}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
