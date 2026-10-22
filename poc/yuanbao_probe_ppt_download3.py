"""Hover the PPT Download control and select the PPTX format."""

from __future__ import annotations

import asyncio
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
        page.on("download", lambda d: downloads.append(d))
        context.on("page", lambda p: popups.append(p))

        await page.goto(CONV_URL, wait_until="domcontentloaded")
        await asyncio.sleep(6)
        await page.locator(".file-card_fileCard__gfdDP").first.click()
        await asyncio.sleep(4)

        btn = page.locator(".toolbar-export-format_download__KVu6U[aria-label='Download']")
        await btn.first.hover()
        await asyncio.sleep(1.5)
        pptx = page.locator("li.t-dropdown__item", has_text="PPTX")
        print("[ppt-dl3] pptx items:", await pptx.count(), flush=True)
        await page.screenshot(path=str(DUMP_DIR / "H5_export_menu.png"))

        async with page.expect_download(timeout=60000) as info:
            await pptx.first.click()
        download = await info.value
        dest = DUMP_DIR / (download.suggested_filename or "presentation.pptx")
        await download.save_as(str(dest))
        head = dest.read_bytes()[:8]
        print("[ppt-dl3] saved:", dest, "bytes:", dest.stat().st_size, "head:", head, flush=True)

        await asyncio.sleep(2)
        print("[ppt-dl3] extra downloads:", len(downloads), "popups:", [p.url for p in popups], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
