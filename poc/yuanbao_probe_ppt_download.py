"""Inspect where the Yuanbao PPT file card leads and how to download it."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
CONV_URL = "https://yuanbao.tencent.com/chat/naQivTmsDa/0RROaWGsMxE"

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  return {
    url: location.href,
    title: document.title,
    bodyText: (document.body.innerText || '').slice(-5000),
    buttons: Array.from(document.querySelectorAll('button,[role="button"],a')).filter(visible)
      .map(el => ({
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
        aria: el.getAttribute('aria-label') || '',
        href: el.getAttribute('href') || '',
        cls: String(el.className || '').slice(0, 160),
      }))
      .filter(x => x.text || x.href).slice(0, 120),
    iframes: Array.from(document.querySelectorAll('iframe')).filter(visible)
      .map(el => (el.src || '').slice(0, 300)),
  };
}
"""


async def main() -> None:
    downloads = []
    new_pages = []

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        def on_download(d):
            downloads.append(d)
            print(f"[ppt-dl] download event: {d.suggested_filename}", flush=True)

        context.on("page", lambda p: new_pages.append(p))
        page.on("download", on_download)

        await page.goto(CONV_URL, wait_until="domcontentloaded")
        await asyncio.sleep(6)
        await page.screenshot(path=str(DUMP_DIR / "H0_ppt_conv.png"))

        card = page.locator(".file-card_fileCard__gfdDP").first
        print("[ppt-dl] card count:", await card.count(), flush=True)
        if await card.count():
            print("[ppt-dl] card html:", (await card.evaluate("el => el.outerHTML"))[:1500], flush=True)
            await card.scroll_into_view_if_needed()
            await card.click()
        await asyncio.sleep(6)

        print("[ppt-dl] downloads:", len(downloads), flush=True)
        print("[ppt-dl] new pages:", [(p.url, await p.title()) for p in new_pages], flush=True)

        for i, d in enumerate(downloads):
            dest = DUMP_DIR / (d.suggested_filename or f"presentation_{i}.pptx")
            try:
                await d.save_as(str(dest))
                print(f"[ppt-dl] saved: {dest} bytes={dest.stat().st_size}", flush=True)
            except Exception as exc:
                print(f"[ppt-dl] save failed: {type(exc).__name__}: {exc}", flush=True)

        targets = [page] + new_pages
        for i, target in enumerate(targets):
            with contextlib.suppress(Exception):
                await target.wait_for_load_state("domcontentloaded", timeout=5000)
            await asyncio.sleep(1)
            info = await target.evaluate(STATE_JS)
            name = "H1_ppt_click_result" if i == 0 else f"H1_ppt_page_{i}"
            (DUMP_DIR / f"{name}.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (DUMP_DIR / f"{name}.html").write_text(
                await target.content(), encoding="utf-8"
            )
            await target.screenshot(path=str(DUMP_DIR / f"{name}.png"))
            print(f"[ppt-dl] page {i} url={info['url']} title={info['title']!r}", flush=True)
            print(f"[ppt-dl] page {i} iframes={info['iframes']}", flush=True)
            for b in info["buttons"][:40]:
                print(f"  btn: {b}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
