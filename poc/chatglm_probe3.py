"""ChatGLM calibration probe #3 (not a test).

Finds the model-select popup: clicks candidate triggers and dumps every
visible popper/overlay panel. Also probes the deep-think toggle button
(.think-mode-trigger) state changes.

    uv run python poc/chatglm_probe3.py
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

POPPERS_JS = """
() => Array.from(document.querySelectorAll(
      '.el-popper, [class*="popper"], [class*="dropdown"], [class*="popover"], [class*="model-select-dropdown"]'))
  .filter((e) => {
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width > 40 && r.height > 40 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  })
  .map((e) => ({
    cls: String(e.className || '').slice(0, 200),
    text: (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 1500),
    html: e.outerHTML.slice(0, 20000),
  }))
"""

THINK_JS = """
() => {
  const el = document.querySelector('.think-mode-trigger');
  if (!el) return { error: 'no .think-mode-trigger' };
  return {
    cls: String(el.className || ''),
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim(),
    html: el.outerHTML.slice(0, 8000),
  };
}
"""


def save(name: str, data) -> None:
    path = DUMP_DIR / name
    path.write_text(
        data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[probe3] wrote {name}", flush=True)


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

        save("think_before.json", await page.evaluate(THINK_JS))

        triggers = [
            ".model-select-name",
            ".model-select-container",
            ".model-select-icon",
            ".think-mode-trigger",
        ]
        for i, sel in enumerate(triggers):
            try:
                await page.locator(sel).first.click(timeout=4000)
                await asyncio.sleep(1.5)
                pop = await page.evaluate(POPPERS_JS)
                save(f"popups_after_click_{i}_{sel.strip('.')}.json", pop)
                await page.screenshot(path=str(DUMP_DIR / f"popups_{i}.png"))
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
            except Exception as exc:  # noqa: BLE001
                print(f"[probe3] trigger {sel} failed: {exc}", flush=True)

        print("[probe3] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
