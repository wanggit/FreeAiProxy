"""Wenxin composer / mode-menu probe (calibration aid).

Assumes the wenxin profile is already logged in. Opens home, then dumps:
  - the composer container HTML (to locate the send button),
  - every element whose text is one of the mode labels,
  - the mode menu after clicking the composer mode button,
  - the composer after typing (to reveal the enabled send control).
Writes JSON + screenshots into poc/dump/wenxin/.

    uv run python poc/wenxin_probe2.py
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
MODE_LABELS = ["对话", "图片生成", "帮我写作", "PPT生成", "更多"]

COMPOSER_JS = """
() => {
  const ta = document.querySelector('textarea.ci-textarea');
  if (!ta) return {found: false};
  let root = ta;
  for (let i = 0; i < 8 && root.parentElement; i++) {
    root = root.parentElement;
    if (root.tagName === 'FORM' || /ci-(input|composer|footer|operate)/.test(String(root.className))) break;
  }
  return {found: true, composerHtml: (root.outerHTML || '').slice(0, 40000)};
}
"""

LABELS_JS = """
(labels) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  const all = Array.from(document.querySelectorAll('*'));
  for (const want of labels) {
    for (const el of all) {
      const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
      if (t !== want) continue;
      if (el.children.length > 2) continue;
      const r = el.getBoundingClientRect();
      out.push({
        want,
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        aria: el.getAttribute('aria-label') || '',
        cls: String(el.className || '').slice(0, 160),
        visible: visible(el),
        rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      });
    }
  }
  return out.slice(0, 120);
}
"""

MENU_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll(
      '[role="menuitem"],[role="option"],[role="menuitemradio"],li,[class*="menu"],[class*="option"],[class*="dropdown"] *')) {
    if (!visible(el)) continue;
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 24) continue;
    const r = el.getBoundingClientRect();
    out.push({
      text: t,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      cls: String(el.className || '').slice(0, 160),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  }
  return out.slice(0, 120);
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
        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.locator("textarea.ci-textarea").first.wait_for(state="visible", timeout=30000)
        await asyncio.sleep(1.5)

        _save("c_composer", await page.evaluate(COMPOSER_JS))
        _save("c_labels", await page.evaluate(LABELS_JS, MODE_LABELS))
        await page.screenshot(path=str(DUMP_DIR / "c_home.png"))

        print("[probe] open mode menu", flush=True)
        mode_btn = page.locator("button.ci-input-mode-button").first
        if await mode_btn.count():
            await mode_btn.click()
            await asyncio.sleep(1.2)
            _save("c_mode_menu", await page.evaluate(MENU_JS))
            await page.screenshot(path=str(DUMP_DIR / "c_mode_menu.png"))
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

        print("[probe] type to reveal send", flush=True)
        await page.locator("textarea.ci-textarea").first.click()
        await page.keyboard.insert_text("测试发送按钮")
        await asyncio.sleep(1.0)
        _save("c_composer_typed", await page.evaluate(COMPOSER_JS))
        _save("c_labels_typed", await page.evaluate(MENU_JS))
        await page.screenshot(path=str(DUMP_DIR / "c_typed.png"))
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")


if __name__ == "__main__":
    asyncio.run(main())
