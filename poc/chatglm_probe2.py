"""ChatGLM interactive calibration probe #2 (not a test).

With a logged-in chatglm profile: inspect the composer (send / deep-think
controls), open the model-select "更多" dropdown and dump the model list,
and record the AI画图 nav target. All dumps go to poc/dump/chatglm/.

    uv run python poc/chatglm_probe2.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "chatglm"
PROFILE_DIR = ROOT / "data" / "profiles" / "chatglm"
START_URL = "https://chatglm.cn/main/alltoolsdetail?lang=zh"

COMPOSER_JS = """
() => {
  const box = document.querySelector('#search-input-box');
  if (!box) return { error: 'no #search-input-box' };
  const dump = (el) => ({
    tag: el.tagName.toLowerCase(),
    cls: String(el.className || '').slice(0, 200),
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    aria: el.getAttribute('aria-label') || '',
    title: el.getAttribute('title') || '',
    ce: el.getAttribute('contenteditable') || '',
  });
  const opts = Array.from(box.querySelectorAll('.options-container *'))
    .filter((e) => {
      const cls = String(e.className || '');
      return e.children.length === 0 || cls.includes('btn') || cls.includes('icon');
    })
    .map(dump);
  const right = Array.from(box.querySelectorAll('.input-box-container > :last-child *'))
    .map(dump);
  return {
    outer: box.outerHTML.length,
    textarea: dump(box.querySelector('textarea') || box.querySelector('[contenteditable]')),
    optionsContainer: opts.slice(0, 40),
    rightSide: right.slice(0, 40),
    fullHtml: box.outerHTML.slice(0, 60000),
  };
}
"""

NAV_JS = """
() => Array.from(document.querySelectorAll('a,[role="menuitem"],.nav-item, [class*="menu"] div'))
  .filter((e) => ['AI画图','AI阅读','AI生视频','新对话','学习搭子'].some((t) => (e.innerText||'').trim() === t))
  .map((e) => ({ tag: e.tagName.toLowerCase(), text: e.innerText.trim(),
                  cls: String(e.className||'').slice(0,150), href: e.getAttribute('href') || '' }))
"""


def save(name: str, data) -> None:
    path = DUMP_DIR / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[probe2] wrote {name}", flush=True)


async def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
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
        await page.wait_for_selector("#search-input-box", timeout=30000)
        await asyncio.sleep(3)

        save("composer.json", await page.evaluate(COMPOSER_JS))
        save("composer.html", (await page.evaluate(COMPOSER_JS))["fullHtml"])
        save("nav.json", await page.evaluate(NAV_JS))

        # open model-select dropdown
        more = page.locator(".model-select-item.more-select-item")
        try:
            await more.first.click(timeout=5000)
        except Exception as exc:  # noqa: BLE001
            print(f"[probe2] model more click failed: {exc}", flush=True)
        await asyncio.sleep(2)
        menus = await page.evaluate(
            """() => Array.from(document.querySelectorAll('.el-dropdown-menu, [role="menu"], [class*="model-select-dropdown"]'))
                 .filter((e) => e.getBoundingClientRect().width > 0)
                 .map((e) => e.outerHTML.slice(0, 40000))"""
        )
        save("model_dropdown.json", menus)
        await page.screenshot(path=str(DUMP_DIR / "model_dropdown.png"))

        # try clicking the model-select icon container too (different trigger?)
        await page.keyboard.press("Escape")
        icon = page.locator(".model-select-icon-container")
        try:
            await icon.first.click(timeout=5000)
            await asyncio.sleep(2)
            menus2 = await page.evaluate(
                """() => Array.from(document.querySelectorAll('.el-dropdown-menu, [role="menu"], [class*="model-select-dropdown"]'))
                     .filter((e) => e.getBoundingClientRect().width > 0)
                     .map((e) => e.outerHTML.slice(0, 40000))"""
            )
            save("model_dropdown2.json", menus2)
            await page.screenshot(path=str(DUMP_DIR / "model_dropdown2.png"))
        except Exception as exc:  # noqa: BLE001
            print(f"[probe2] model icon click failed: {exc}", flush=True)

        print("[probe2] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
