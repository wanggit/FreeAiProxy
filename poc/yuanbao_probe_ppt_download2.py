"""Click the PPT workspace Download control and capture the file."""

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


async def main() -> None:
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(CONV_URL, wait_until="domcontentloaded")
        await asyncio.sleep(6)
        await page.locator(".file-card_fileCard__gfdDP").first.click()
        await asyncio.sleep(4)

        download_btn = page.locator(
            ".toolbar-export-format_download__KVu6U[aria-label='Download']"
        )
        print("[ppt-dl2] download button count:", await download_btn.count(), flush=True)
        await page.screenshot(path=str(DUMP_DIR / "H2_before_download.png"))

        try:
            async with page.expect_download(timeout=20000) as info:
                await download_btn.first.click()
            download = await info.value
            dest = DUMP_DIR / download.suggested_filename
            await download.save_as(str(dest))
            print("[ppt-dl2] saved:", dest, "bytes:", dest.stat().st_size, flush=True)
            print("[ppt-dl2] header:", dest.read_bytes()[:8], flush=True)
        except Exception as exc:
            print("[ppt-dl2] no direct download:", type(exc).__name__, exc, flush=True)
            await page.screenshot(path=str(DUMP_DIR / "H3_after_download_click.png"))
            text = await page.evaluate(
                """() => Array.from(document.querySelectorAll(
                    'button,[role="button"],[role="menuitem"],a,[aria-label]'))
                    .filter(el => el.getBoundingClientRect().width > 0)
                    .map(el => ({
                      tag: el.tagName.toLowerCase(),
                      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 100),
                      aria: el.getAttribute('aria-label') || '',
                      href: el.getAttribute('href') || '',
                    }))
                    .filter(x => /download|export|pdf|pptx?|下载|导出/i.test(x.text + ' ' + x.aria + ' ' + x.href))
                    .slice(0, 60)"""
            )
            print("[ppt-dl2] visible download/export elements:", json.dumps(text, ensure_ascii=False), flush=True)
            (DUMP_DIR / "H3_after_download_click.json").write_text(
                json.dumps(text, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (DUMP_DIR / "H3_after_download_click.html").write_text(
                await page.content(), encoding="utf-8"
            )

        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
