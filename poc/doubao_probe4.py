"""Doubao skill option probe #4 (calibration aid, not a test).

Enters a generation skill (image / video / music) and opens every option
control the composer exposes (模型 / 比例 / 风格 / 时长 / 清晰度 / ...), dumping
the expanded menu after each click so the option vocabularies can be read off
the DOM. Nothing is sent.

    uv run python poc/doubao_probe4.py image
    uv run python poc/doubao_probe4.py video
    uv run python poc/doubao_probe4.py music

Dumps land in poc/dump/doubao/<mode>/opt/ .
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

sys.path.insert(0, str(ROOT / "poc"))  # noqa: E402
from doubao_probe3 import START_URL, dump, enter_skill  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"

OPTION_RE = re.compile(r"比例|风格|模型|时长|清晰度|分辨率|画幅|运镜|快速|智能|模板|自动|·|s$")
SKIP_RE = re.compile(r"^(对话|图像生成|视频生成|音乐生成|PPT 生成|帮我写作|AI 播客|录音转写|更多|发送|\+)$")


async def option_buttons(page) -> list[dict]:  # noqa: ANN001
    return await page.evaluate(
        """
        () => Array.from(document.querySelectorAll(
            '[data-testid="chat_input"] button, [data-testid="chat_input"] [role="button"]'))
          .map((el, i) => ({
            i,
            text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
            cls: String(el.className || '').slice(0, 160),
            aria: el.getAttribute('aria-label') || '',
            html: (el.outerHTML || '').replace(/\\s+/g, ' ').slice(0, 400),
          }))
        """
    )


async def open_menu_dump(page, out_dir: Path, idx: int, label: str) -> None:
    try:
        btn = page.locator(
            '[data-testid="chat_input"] button, [data-testid="chat_input"] [role="button"]'
        ).nth(idx)
        await btn.click()
    except Exception as exc:  # noqa: BLE001
        print(f"[opt] click {label} failed: {exc}", flush=True)
        return
    await asyncio.sleep(1.5)
    await dump(page, out_dir, f"menu_{idx:02d}_{label}")
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.8)


MUSIC_CHIP_SEL = '[data-testid="chat_input_input"] [data-slot="dropdown-menu-trigger"]'


async def music_chips(page) -> int:  # noqa: ANN001
    return await page.locator(MUSIC_CHIP_SEL).count()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["image", "video", "music", "chat"])
    args = ap.parse_args()

    out_dir = DUMP_ROOT / args.mode / "opt"
    out_dir.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("HEADLESS") == "1"

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        humanize=True,
        os="macos",
        locale="zh-CN",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(START_URL, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[opt] goto failed: {exc}", flush=True)
        await asyncio.sleep(4)
        await enter_skill(page, args.mode)
        await asyncio.sleep(2)
        info = await dump(page, out_dir, "00_base")

        if args.mode == "music":
            n = await music_chips(page)
            print(f"[opt] music chips: {n}", flush=True)
            for i in range(n):
                try:
                    await page.locator(MUSIC_CHIP_SEL).nth(i).click()
                except Exception as exc:  # noqa: BLE001
                    print(f"[opt] chip {i} failed: {exc}", flush=True)
                    continue
                await asyncio.sleep(1.2)
                await dump(page, out_dir, f"chip_{i:02d}")
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.8)
            await dump(page, out_dir, "99_after")
            return

        clicked: set[str] = set()
        for btn in await option_buttons(page):
            text = btn["text"]
            norm = text.replace("×", "").strip()
            if not text or SKIP_RE.match(norm) or not OPTION_RE.search(text) or text in clicked:
                continue
            clicked.add(text)
            await open_menu_dump(page, out_dir, btn["i"], re.sub(r"\W+", "", text)[:20])

        # Second pass: the composer may re-render and expose extra controls.
        await dump(page, out_dir, "99_after")
        print(f"[opt] clicked: {sorted(clicked)}", flush=True)
        print(f"[opt] body tail: {info['bodyText'][-300:]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
