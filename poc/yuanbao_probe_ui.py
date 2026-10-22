"""Probe Yuanbao menus and mode entries without sending a message.

Reuses the logged-in calibration profile. Opens the model switcher, AI Image,
Writing, More and Add menus, dumping a screenshot + inventory for each.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
START_URL = "https://yuanbao.tencent.com/"

INVENTORY_JS = """
() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0
      && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  };
  const txt = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    text: (txt(el) || el.getAttribute('aria-label') || '').slice(0, 160),
    cls: String(el.className || '').slice(0, 260),
    role: el.getAttribute('role') || '',
    aria: el.getAttribute('aria-label') || '',
    title: el.getAttribute('title') || '',
    placeholder: el.getAttribute('placeholder') || '',
    type: el.getAttribute('type') || '',
    href: el.getAttribute('href') || '',
  });
  const clickables = Array.from(document.querySelectorAll(
      'button,[role="button"],[role="menuitem"],[role="option"],[role="tab"],a'))
    .filter(visible).map(desc).slice(0, 300);
  const editables = Array.from(document.querySelectorAll(
      'textarea,input,[contenteditable="true"],[contenteditable=""],[role="textbox"]'))
    .filter(visible).map(desc).slice(0, 40);
  const fileInputs = Array.from(document.querySelectorAll('input[type="file"]')).map(el => ({
    cls: String(el.className || ''),
    accept: el.accept || '',
    multiple: el.multiple,
    hidden: !visible(el),
  }));
  const editor = document.querySelector('.ql-editor');
  const chain = [];
  let node = editor;
  while (node && chain.length < 14) {
    chain.push({
      tag: node.tagName.toLowerCase(),
      cls: String(node.className || '').slice(0, 200),
      id: node.id || '',
    });
    node = node.parentElement;
  }
  const composerRoot = editor ? editor.closest('form') || editor.parentElement?.parentElement : null;
  return {
    url: location.href,
    title: document.title,
    bodyText: (document.body.innerText || '').slice(0, 8000),
    clickables,
    editables,
    fileInputs,
    editorChain: chain,
    composerHtml: composerRoot ? composerRoot.outerHTML.slice(0, 30000) : '',
  };
}
"""


async def wait_home(page) -> None:
    await page.goto(START_URL, wait_until="domcontentloaded")
    await page.locator(".ql-editor").first.wait_for(state="visible", timeout=30000)
    await asyncio.sleep(1.5)


async def dump(page, name: str, info: dict | None = None) -> dict:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    info = info or await page.evaluate(INVENTORY_JS)
    (DUMP_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    await page.screenshot(path=str(DUMP_DIR / f"{name}.png"), full_page=False)
    if info.get("composerHtml"):
        (DUMP_DIR / f"{name}_composer.html").write_text(
            info["composerHtml"], encoding="utf-8"
        )
    print(f"[dump] {name}: url={info.get('url')} clickables={len(info.get('clickables', []))} "
          f"editables={len(info.get('editables', []))} files={len(info.get('fileInputs', []))}",
          flush=True)
    return info


async def click_visible(page, name: str, *, role: str = "button") -> bool:
    loc = page.get_by_role(role, name=name, exact=True)
    for i in range(await loc.count()):
        item = loc.nth(i)
        if await item.is_visible():
            await item.click()
            await asyncio.sleep(1.2)
            return True
    return False


async def main() -> None:
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await wait_home(page)
        await dump(page, "10_home")

        print("[probe] model switcher", flush=True)
        await click_visible(page, "Switch model")
        await dump(page, "11_model_menu")
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

        print("[probe] AI Image", flush=True)
        await click_visible(page, "AI Image")
        await asyncio.sleep(2.0)
        await dump(page, "12_ai_image")

        await wait_home(page)
        print("[probe] Writing", flush=True)
        await click_visible(page, "Writing")
        await asyncio.sleep(2.0)
        await dump(page, "13_writing")

        await wait_home(page)
        print("[probe] More", flush=True)
        await click_visible(page, "More")
        await dump(page, "14_more")
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

        print("[probe] Add", flush=True)
        await click_visible(page, "Add")
        await dump(page, "15_add")
        await page.keyboard.press("Escape")

        print("[probe] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
