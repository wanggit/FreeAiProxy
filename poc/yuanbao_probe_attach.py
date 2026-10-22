"""Probe Yuanbao image/document attachments and multimodal answers."""

from __future__ import annotations

import asyncio
import json
import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
START_URL = "https://yuanbao.tencent.com/"

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const attachish = Array.from(document.querySelectorAll(
      '[class*="attach"],[class*="file"],[class*="upload"],[class*="image-preview"],[class*="img-preview"]'))
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 140),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
    }))
    .filter(x => x.text).slice(0, 60);
  const imgs = Array.from(document.querySelectorAll('img')).filter(visible)
    .map(el => ({alt: el.alt || '', src: (el.currentSrc || el.src || '').slice(0, 80),
                  w: Math.round(el.getBoundingClientRect().width),
                  h: Math.round(el.getBoundingClientRect().height)}))
    .filter(x => x.w >= 24 && x.h >= 24).slice(0, 40);
  const markdowns = Array.from(document.querySelectorAll('.hyc-common-markdown')).filter(visible)
    .map(el => ({len: (el.innerText || '').length, text: (el.innerText || '').slice(0, 300),
                 done: !!el.closest('.hyc-content-md-done')}));
  return {
    url: location.href,
    bodyText: (document.body.innerText || '').slice(0, 3000),
    attachish,
    imgs,
    markdowns,
    doneCount: document.querySelectorAll('.hyc-content-md-done').length,
    sendVisible: !!Array.from(document.querySelectorAll('[aria-label="Send"]')).find(visible),
    stopVisible: !!Array.from(document.querySelectorAll('[aria-label="Stop Answering"]')).find(visible),
    loginSurfaces: document.querySelectorAll('[class*="hyc-login"]').length,
  };
}
"""


def make_solid_png(path: Path, rgb: tuple[int, int, int], size: int = 512) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


async def js_click(page, selector: str) -> bool:
    return bool(await page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            el.click();
            return true;
        }""",
        selector,
    ))


async def click_visible(page, name: str, role: str = "button") -> bool:
    loc = page.get_by_role(role, name=name, exact=True)
    for i in range(await loc.count()):
        item = loc.nth(i)
        if await item.is_visible():
            await item.click()
            await asyncio.sleep(0.4)
            return True
    return False


async def goto_home(page) -> None:
    await page.goto(START_URL, wait_until="domcontentloaded")
    await page.locator(".ql-editor").first.wait_for(state="visible", timeout=30000)
    await page.locator("button[aria-label='Add']").first.wait_for(
        state="visible", timeout=30000
    )
    await asyncio.sleep(1.5)


async def dump(page, name: str, info: dict | None = None) -> dict:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    info = info or await page.evaluate(STATE_JS)
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DUMP_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(DUMP_DIR / f"{name}.png"), full_page=False)
    print(f"[dump] {name}: md={[(m['len'], m['done']) for m in info.get('markdowns', [])]} "
          f"done={info.get('doneCount')} send={info.get('sendVisible')}", flush=True)
    return info


async def upload_via_menu(page, menu_item: str, file_path: Path, name: str) -> bool:
    await js_click(page, "button[aria-label='Add']")
    await asyncio.sleep(0.7)
    try:
        async with page.expect_file_chooser(timeout=8000) as chooser_info:
            clicked = await click_visible(page, menu_item, role="menuitem")
        if not clicked:
            print(f"[{name}] menu item not clickable", flush=True)
            return False
        chooser = await chooser_info.value
        await chooser.set_files(str(file_path))
        print(f"[{name}] file chooser accepted {file_path.name}", flush=True)
    except Exception as exc:
        print(f"[{name}] file chooser failed: {type(exc).__name__}: {exc}", flush=True)
        return False

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        info = await page.evaluate(STATE_JS)
        if file_path.name in info["bodyText"] or info["imgs"]:
            await dump(page, f"{name}_attached", info)
            return True
        print(f"[{name}] uploading... attach={len(info['attachish'])} imgs={len(info['imgs'])}",
              flush=True)
    await dump(page, f"{name}_attach_timeout")
    return False


async def send_prompt(page, prompt: str, label: str) -> dict:
    before = (await page.evaluate(STATE_JS))["doneCount"]
    await page.locator(".ql-editor").first.click()
    await page.keyboard.insert_text(prompt)
    await asyncio.sleep(0.5)
    await page.locator("[aria-label='Send']").first.click()
    print(f"[{label}] sent", flush=True)

    t0 = time.monotonic()
    last_md = ""
    stable = 0
    info = {}
    while time.monotonic() - t0 < 120:
        await asyncio.sleep(0.5)
        info = await page.evaluate(STATE_JS)
        md = "|".join(m["text"] for m in info["markdowns"])
        if md == last_md:
            stable += 1
        else:
            stable = 0
            last_md = md
        if int(time.monotonic() - t0) % 2 == 0:
            print(f"[{label}] t={time.monotonic()-t0:5.1f} done={info['doneCount']} "
                  f"send={info['sendVisible']} stop={info['stopVisible']} "
                  f"md={[(m['len'], m['done']) for m in info['markdowns']]}", flush=True)
        if info["doneCount"] > before and info["sendVisible"] and not info["stopVisible"] and stable >= 4:
            break

    await dump(page, f"{label}_final", info)
    return info


async def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    image_path = DUMP_DIR / "sample_red_512.png"
    make_solid_png(image_path, (220, 30, 30))
    doc_path = DUMP_DIR / "sample_yuanbao.txt"
    doc_path.write_text(
        "FreeAiProxy 元宝标定附件。\n代号：蓝鸟计划\n数字：7391\n", encoding="utf-8"
    )

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        await goto_home(page)
        if await click_visible(page, "Got It"):
            await asyncio.sleep(0.3)
        ok = await upload_via_menu(page, "Upload Image", image_path, "50_image")
        print("[image] upload ok:", ok, flush=True)
        if ok:
            await send_prompt(page, "这张图片主要是什么颜色？只回答颜色。", "51_image_answer")

        await goto_home(page)
        if await click_visible(page, "Got It"):
            await asyncio.sleep(0.3)
        ok = await upload_via_menu(page, "Local Files", doc_path, "52_doc")
        print("[doc] upload ok:", ok, flush=True)
        if ok:
            await send_prompt(
                page, "请读取附件，告诉我代号和数字，只输出这两项。", "53_doc_answer"
            )


if __name__ == "__main__":
    asyncio.run(main())
