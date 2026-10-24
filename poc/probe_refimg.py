"""Probe reference-image upload entries on each provider's image-generation page.

Uses an APFS clone of the login profile so the running gateway is untouched:

    uv run python poc/probe_refimg.py doubao
    uv run python poc/probe_refimg.py yuanbao --upload
    uv run python poc/probe_refimg.py wenxin --upload
    uv run python poc/probe_refimg.py chatglm --upload
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

from freeaiproxy.engine.browser_manager import _host_os  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump"
PROFILES = {
    "yuanbao": ROOT / "data" / "profiles" / "yuanbao",
    "doubao": ROOT / "data" / "profiles" / "doubao",
    "wenxin": ROOT / "data" / "profiles" / "wenxin",
    "chatglm": ROOT / "data" / "profiles" / "chatglm",
}
START_URLS = {
    "yuanbao": "https://yuanbao.tencent.com/",
    "doubao": "https://www.doubao.com/chat/",
    "wenxin": "https://wenxin.baidu.com/",
    "chatglm": "https://chatglm.cn/main/gdetail/65a232c082ff90a2ad2f15e2?lang=zh",
}
LOCALES = {"yuanbao": "en-US", "doubao": "zh-CN", "wenxin": "zh-CN", "chatglm": "zh-CN"}

PROBE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const fileInputs = Array.from(document.querySelectorAll('input[type="file"]')).map(el => ({
    accept: el.accept || '',
    multiple: !!el.multiple,
    id: el.id || '',
    cls: String(el.className || '').slice(0, 120),
    testid: el.getAttribute('data-testid') || '',
    visible: visible(el),
    parentTestid: (el.closest('[data-testid]') || {}).dataset?.testid || '',
    parentCls: String((el.parentElement || {}).className || '').slice(0, 120),
  }));
  const triggers = Array.from(document.querySelectorAll(
      '[aria-label],[title],[data-testid],[class*="upload"],[class*="add"],[class*="plus"],[class*="attach"]'))
    .filter(el => visible(el))
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      aria: el.getAttribute('aria-label') || '',
      title: el.getAttribute('title') || '',
      testid: el.getAttribute('data-testid') || '',
      cls: String(el.className || '').slice(0, 120),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 40),
    }))
    .filter(x => /upload|attach|add|plus|image|file|上传|图片|附件|添加|参考/i.test(
      `${x.aria} ${x.title} ${x.testid} ${x.cls} ${x.text}`))
    .slice(0, 40);
  const blobImgs = Array.from(document.querySelectorAll('img'))
    .filter(el => (el.src || '').startsWith('blob:'))
    .map(el => ({alt: el.alt || '', w: Math.round(el.getBoundingClientRect().width)}));
  const vh = window.innerHeight;
  const bottomImgs = Array.from(document.querySelectorAll('img'))
    .map(el => { const r = el.getBoundingClientRect();
      return {src: (el.currentSrc || el.src || '').slice(0, 60), alt: el.alt || '',
              y: Math.round(r.y), w: Math.round(r.width)}; })
    .filter(x => x.y > vh - 320 && x.w >= 24 && x.w <= 200);
  return {
    url: location.href,
    fileInputs,
    triggers,
    blobImgs,
    bottomImgs,
    bodyHasSample: false,
    composerVisible: !!document.querySelector(
      '.ql-editor,[contenteditable="true"],textarea,div.ci-editor,#search-input-box'),
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


def clone_profile(provider: str) -> Path:
    src = PROFILES[provider]
    dst = Path(f"/tmp/freeaiproxy-probe-{provider}")
    subprocess.run(["rm", "-rf", str(dst)], check=False)
    subprocess.run(["cp", "-Rc", str(src), str(dst)], check=True)
    subprocess.run(["rm", "-f", str(dst / "parent.lock")], check=False)
    for lck in dst.glob("*.lck"):
        lck.unlink(missing_ok=True)
    return dst


async def dump(page, out_dir: Path, name: str) -> dict:
    info = await page.evaluate(PROBE_JS)
    (out_dir / f"{name}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    await page.screenshot(path=str(out_dir / f"{name}.png"))
    print(
        f"[dump {name}] inputs={len(info['fileInputs'])} "
        f"visible={sum(1 for x in info['fileInputs'] if x['visible'])} "
        f"triggers={len(info['triggers'])} blob={len(info['blobImgs'])}",
        flush=True,
    )
    for x in info["fileInputs"]:
        print(f"   input accept={x['accept']!r} visible={x['visible']} "
              f"testid={x['testid']!r} parent={x['parentTestid'] or x['parentCls'][:60]!r}",
              flush=True)
    for x in info["triggers"]:
        print(f"   trigger aria={x['aria']!r} title={x['title']!r} testid={x['testid']!r} "
              f"text={x['text']!r} cls={x['cls'][:60]!r}", flush=True)
    for x in info.get("bottomImgs", []):
        print(f"   bottomImg src={x['src']!r} alt={x['alt']!r} y={x['y']} w={x['w']}", flush=True)
    return info


async def click_text(page, text: str, exact: bool = True) -> bool:
    loc = page.get_by_text(text, exact=exact)
    for i in range(min(await loc.count(), 5)):
        item = loc.nth(i)
        if await item.is_visible():
            try:
                await item.click(timeout=6000)
            except Exception:
                await item.evaluate("el => el.click()")
            await asyncio.sleep(0.6)
            return True
    return False


async def click_aria(page, name: str) -> bool:
    loc = page.locator(f"[aria-label='{name}']")
    for i in range(await loc.count()):
        item = loc.nth(i)
        if await item.is_visible():
            try:
                await item.click(timeout=6000)
            except Exception:
                await item.evaluate("el => el.click()")
            await asyncio.sleep(0.6)
            return True
    return False


PLACEHOLDER_JS = """
(sub) => {
  const editor = document.querySelector('.ql-editor');
  const placeholder = editor ? (editor.getAttribute('data-placeholder') || '') : '';
  if (placeholder.includes(sub)) return true;
  return (document.body.innerText || '').includes(sub);
}
"""


async def enter_image_mode(page, provider: str) -> None:
    if provider == "yuanbao":
        await page.goto(START_URLS[provider], wait_until="domcontentloaded")
        await page.locator(".ql-editor").first.wait_for(state="visible", timeout=45000)
        await click_text(page, "Got It")
        if not await page.evaluate(PLACEHOLDER_JS, "Describe the image"):
            try:
                await page.wait_for_selector(
                    "[aria-label='AI Image']", state="attached", timeout=30000
                )
            except Exception:
                print("[yuanbao] AI Image button never attached", flush=True)
            if not await click_aria(page, "AI Image"):
                await click_text(page, "AI Image", exact=False)
            for _ in range(60):
                if await page.evaluate(PLACEHOLDER_JS, "Describe the image"):
                    break
                await asyncio.sleep(0.5)
            else:
                await page.screenshot(path=str(DUMP_ROOT / "refimg_yuanbao" / "00_enter_fail.png"))
                body = (await page.evaluate("() => document.body.innerText.slice(0, 800)"))
                print(f"[yuanbao] failed to enter AI Image; body starts: {body!r}", flush=True)
                raise SystemExit(1)
    elif provider == "doubao":
        await page.goto(START_URLS[provider], wait_until="domcontentloaded")
        await page.locator('[data-testid="chat_input"]').first.wait_for(
            state="attached", timeout=45000
        )
        await asyncio.sleep(2)
        await page.locator('[data-testid="skill_bar_button_3"]').first.click(timeout=15000)
        await page.locator('[data-testid="chat_input_input"] [contenteditable="true"]').first.wait_for(
            state="visible", timeout=20000
        )
    elif provider == "wenxin":
        await page.goto(START_URLS[provider], wait_until="domcontentloaded")
        await page.locator("textarea.ci-textarea, div.ci-editor").first.wait_for(
            state="visible", timeout=45000
        )
        await asyncio.sleep(1.5)
        await click_text(page, "图片生成", exact=True)
        await page.locator("div.ci-editor").first.wait_for(state="visible", timeout=20000)
    elif provider == "chatglm":
        await page.goto(START_URLS[provider], wait_until="domcontentloaded")
        await page.locator("#search-input-box textarea").first.wait_for(
            state="visible", timeout=45000
        )
    await asyncio.sleep(2.5)


def _is_image_input(info: dict) -> bool:
    accept = str(info["accept"]).lower()
    if not accept:
        return True
    return "image" in accept or any(
        ext in accept for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".apng")
    )


async def _chooser_upload(page, opener, png: Path, out_dir: Path, tag: str) -> bool:
    """Click `opener` (a callable) and feed the file chooser."""
    try:
        async with page.expect_file_chooser(timeout=8000) as chooser_info:
            await opener()
        chooser = await chooser_info.value
        await chooser.set_files(str(png))
        await asyncio.sleep(3)
        await dump(page, out_dir, f"02_after_{tag}")
        return True
    except Exception as exc:
        print(f"[upload] {tag} failed: {type(exc).__name__}: {exc}", flush=True)
        await page.keyboard.press("Escape")
        return False


async def try_upload(page, provider: str, png: Path, out_dir: Path) -> None:
    info = await page.evaluate(PROBE_JS)
    inputs = [x for x in info["fileInputs"] if _is_image_input(x)]
    for idx in range(len(inputs)):
        loc = page.locator('input[type="file"]').nth(idx)
        try:
            await loc.set_input_files(str(png), timeout=8000)
        except Exception as exc:
            print(f"[upload] input#{idx} set_input_files failed: {exc}", flush=True)
            continue
        await asyncio.sleep(3)
        after = await page.evaluate(PROBE_JS)
        print(f"[upload] input#{idx} accepted; blob={len(after['blobImgs'])}", flush=True)
        await dump(page, out_dir, "02_after_direct_upload")
        return

    if provider == "yuanbao" and await page.locator("[aria-label='Upload Image']").count():
        if await _chooser_upload(page, lambda: page.locator(
                "[aria-label='Upload Image']").first.click(timeout=6000),
                png, out_dir, "upload_image_button"):
            return

        async def open_add_menu_and_pick() -> None:
            await click_aria(page, "Add")
            await asyncio.sleep(0.8)
            loc = page.get_by_role("menuitem", name="Upload Image")
            for i in range(await loc.count()):
                item = loc.nth(i)
                if await item.is_visible():
                    box = await item.bounding_box()
                    if box:
                        await page.mouse.click(box["x"] + box["width"] / 2,
                                               box["y"] + box["height"] / 2)
                    else:
                        await item.evaluate("el => el.click()")
                    return
            await page.keyboard.press("Escape")
            raise RuntimeError("no visible Upload Image menuitem in Add menu")

        if await _chooser_upload(page, open_add_menu_and_pick, png, out_dir, "add_menu"):
            return

    for sel in ('[data-testid="upload_file_button"]', "[aria-label*='upload' i]",
                "[aria-label*='Upload' i]", "[title*='上传']"):
        loc = page.locator(sel)
        if await loc.count():
            async def opener(sel=sel, loc=loc) -> None:
                item = loc.first
                box = await item.bounding_box()
                if box:
                    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                else:
                    await item.evaluate("el => el.click()")
            if await _chooser_upload(page, opener, png, out_dir, f"trigger_{sel[:20]}"):
                return
    print("[upload] nothing worked; dumping candidates", flush=True)
    await dump(page, out_dir, "02_upload_candidates")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", choices=list(PROFILES))
    ap.add_argument("--upload", action="store_true", help="also attempt uploading a test PNG")
    ap.add_argument("--headed", action="store_true", default=True)
    args = ap.parse_args()

    provider = args.provider
    out_dir = DUMP_ROOT / f"refimg_{provider}"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    png = out_dir / "sample_red.png"
    make_solid_png(png, (220, 30, 30))

    profile = clone_profile(provider)
    print(f"[{provider}] profile clone: {profile}", flush=True)

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(profile),
        headless=False,
        humanize=True,
        os=_host_os(),
        locale=LOCALES[provider],
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await enter_image_mode(page, provider)
        info = await dump(page, out_dir, "01_image_mode")
        if not info["composerVisible"]:
            print(f"[{provider}] composer missing; maybe login expired or UI changed", flush=True)
        if args.upload:
            await try_upload(page, provider, png, out_dir)
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
