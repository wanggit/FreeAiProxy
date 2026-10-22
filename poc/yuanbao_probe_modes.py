"""Check whether Yuanbao restores Search / Writing / AI Image mode on reload."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
BASE = "https://yuanbao.tencent.com/chat/naQivTmsDa/"
CONVS = {
    "search": BASE + "0RRMzaQ1QY4",
    "image": BASE + "0RRNL7U2xI8",
    "writing": BASE + "0RRNRZs6hcG",
}

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const root = document.querySelector('.agent-dialogue__content--common__input')
    || document.querySelector('.ql-editor')?.closest('[class*="input"]')
    || document.body;
  const controls = Array.from(root.querySelectorAll('button,[role="button"]'))
    .filter(visible)
    .map(el => ({
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 60),
      aria: el.getAttribute('aria-label') || '',
      cls: String(el.className || '').slice(0, 120),
    }))
    .filter(x => x.text || x.aria);
  const editor = document.querySelector('.ql-editor');
  return {
    url: location.href,
    placeholder: editor ? editor.getAttribute('data-placeholder') || '' : '',
    controls,
    bodyText: (document.body.innerText || '').slice(0, 600),
    hasSearchPill: !!Array.from(root.querySelectorAll('*')).find(el =>
      visible(el) && (el.innerText || '').trim() === 'Search'),
    hasWritingPill: !!Array.from(root.querySelectorAll('*')).find(el =>
      visible(el) && (el.innerText || '').trim() === 'Writing'),
    hasImagePill: !!Array.from(root.querySelectorAll('*')).find(el =>
      visible(el) && (el.innerText || '').trim() === 'AI Image'),
  };
}
"""


async def main() -> None:
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        for name, url in CONVS.items():
            await page.goto(url, wait_until="domcontentloaded")
            await page.locator(".ql-editor").first.wait_for(state="visible", timeout=30000)
            await asyncio.sleep(3)
            info = await page.evaluate(STATE_JS)
            (DUMP_DIR / f"90_{name}_mode.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await page.screenshot(path=str(DUMP_DIR / f"90_{name}_mode.png"))
            pills = [c["text"] for c in info["controls"] if c["text"] in ("Search", "Writing", "AI Image")]
            switch = [c["text"] for c in info["controls"] if c["aria"] == "Switch model"]
            print(f"[{name}] pills={pills} switch={switch} "
                  f"placeholder={info['placeholder']!r}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
