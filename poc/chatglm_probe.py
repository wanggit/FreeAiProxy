"""ChatGLM (智谱清言) login/home probe (calibration aid, not a test).

Opens a HEADED chatglm.cn browser on the persisted chatglm profile, then every
interval dumps the current page inventory (visible inputs/buttons + text),
an HTML snapshot and a screenshot into poc/dump/chatglm/. Log in through the
window; read the dumps to extract selectors; stop the script once captured —
the login state persists in the profile for later stages.

    uv run python poc/chatglm_probe.py            # ~15 min watch loop
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

DUMP_DIR = ROOT / "poc" / "dump" / "chatglm"
PROFILE_DIR = ROOT / "data" / "profiles" / "chatglm"
START_URL = "https://chatglm.cn/"
INTERVAL_S = 8
MAX_S = 15 * 60

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
    text: (txt(el) || el.getAttribute('aria-label') || '').slice(0, 120),
    cls: String(el.className || '').slice(0, 220),
    role: el.getAttribute('role') || '',
    aria: el.getAttribute('aria-label') || '',
    title: el.getAttribute('title') || '',
    placeholder: el.getAttribute('placeholder') || '',
    type: el.getAttribute('type') || '',
    href: (el.getAttribute('href') || '').slice(0, 120),
    ce: el.getAttribute('contenteditable') || '',
  });
  const clickables = Array.from(document.querySelectorAll(
      'button,[role="button"],[role="menuitem"],[role="option"],[role="tab"],a'))
    .filter(visible).map(desc).slice(0, 250);
  const editables = Array.from(document.querySelectorAll(
      'textarea,input,[contenteditable="true"],[contenteditable=""],[role="textbox"]'))
    .filter(visible).map(desc).slice(0, 40);
  return {
    url: location.href,
    title: document.title,
    bodyText: (document.body.innerText || '').slice(0, 6000),
    clickables,
    editables,
  };
}
"""


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
        try:
            await page.goto(START_URL, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[probe] initial goto failed: {exc}", flush=True)

        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < MAX_S:
            n += 1
            try:
                info = await page.evaluate(INVENTORY_JS)
                (DUMP_DIR / "home.json").write_text(
                    json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (DUMP_DIR / "home.html").write_text(
                    await page.content(), encoding="utf-8"
                )
                await page.screenshot(path=str(DUMP_DIR / "home.png"), full_page=False)
                print(
                    f"[probe] #{n} url={info.get('url')} "
                    f"clickables={len(info.get('clickables', []))} "
                    f"editables={len(info.get('editables', []))}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[probe] #{n} evaluate failed: {exc}", flush=True)
            await asyncio.sleep(INTERVAL_S)
        print("[probe] window closed after timeout", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
