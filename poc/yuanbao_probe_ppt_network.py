"""Observe network traffic when clicking the PPT workspace Download control."""

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
CONV_URL = "https://yuanbao.tencent.com/chat/naQivTmsDa/0RROaWGsMxE"

KEYS = ("download", "export", "file", "ppt", "doc", "pdf", "workspace")


def interesting(url: str) -> bool:
    low = url.lower()
    return any(k in low for k in KEYS)


async def main() -> None:
    reqs = []
    resps = []
    downloads = []
    popups = []

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        def on_request(req):
            if interesting(req.url):
                reqs.append((req.method, req.url, (req.post_data or "")[:300]))
                print(f"[net] REQ {req.method} {req.url[:220]}", flush=True)

        def on_response(resp):
            ct = resp.headers.get("content-type", "")
            cd = resp.headers.get("content-disposition", "")
            if interesting(resp.url) or any(k in ct.lower() for k in ("octet", "ppt", "pdf")):
                resps.append((resp.status, ct, cd, resp.url))
                print(f"[net] RESP {resp.status} ct={ct[:60]} cd={cd[:80]} {resp.url[:220]}",
                      flush=True)

        def on_download(d):
            downloads.append(d)
            print(f"[net] DOWNLOAD {d.suggested_filename}", flush=True)

        def on_page(p):
            popups.append(p)
            print(f"[net] POPUP {p.url}", flush=True)

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("download", on_download)
        context.on("page", on_page)

        await page.goto(CONV_URL, wait_until="domcontentloaded")
        await asyncio.sleep(6)
        await page.locator(".file-card_fileCard__gfdDP").first.click()
        await asyncio.sleep(4)

        btn = page.locator(".toolbar-export-format_download__KVu6U[aria-label='Download']")
        print("[net] button count:", await btn.count(), flush=True)
        box = await btn.first.bounding_box()
        print("[net] button box:", box, flush=True)

        await btn.first.hover()
        await asyncio.sleep(2)
        await page.screenshot(path=str(DUMP_DIR / "H4_download_hover.png"))
        menu = await page.evaluate(
            """() => Array.from(document.querySelectorAll(
                'button,[role="button"],[role="menuitem"],a,li,div[class*="menu"],div[class*="dropdown"]'))
                .filter(el => {
                  const r = el.getBoundingClientRect();
                  const s = getComputedStyle(el);
                  return r.width > 0 && r.height > 0 && s.display !== 'none';
                })
                .map(el => ({
                  tag: el.tagName.toLowerCase(),
                  text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 100),
                  aria: el.getAttribute('aria-label') || '',
                  cls: String(el.className || '').slice(0, 140),
                }))
                .filter(x => /pdf|ppt|pptx|download|export|format/i.test(x.text + ' ' + x.aria + ' ' + x.cls))
                .slice(0, 80)"""
        )
        print("[net] hover elements:", json.dumps(menu, ensure_ascii=False), flush=True)

        await btn.first.click(force=True)
        print("[net] clicked", flush=True)
        await asyncio.sleep(8)

        print("[net] reqs:", len(reqs), "resps:", len(resps), "downloads:", len(downloads),
              "popups:", len(popups), flush=True)
        for d in downloads:
            dest = DUMP_DIR / d.suggested_filename
            try:
                await d.save_as(str(dest))
                print("[net] saved", dest, dest.stat().st_size, flush=True)
            except Exception as exc:
                print("[net] save failed", exc, flush=True)
        for p in popups:
            print("[net] popup url:", p.url, flush=True)

        (DUMP_DIR / "H4_network.json").write_text(
            json.dumps({"reqs": reqs, "resps": resps, "menu": menu}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await page.screenshot(path=str(DUMP_DIR / "H4_download_after.png"))


if __name__ == "__main__":
    asyncio.run(main())
