"""ChatGLM calibration probe #8 (not a test) — download paths + watermark A/B.

Opens the image editor dialog and captures what 下载图片 does (Playwright
download? blob? new tab?), both with 去水印 off and on, plus the raw CDN jpg.

    uv run python poc/chatglm_probe8.py
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
CID_URL = "https://chatglm.cn/main/gdetail/65a232c082ff90a2ad2f15e2?lang=zh&cid=6adad3b9cc29bb9c73b3089b"
RAW_JPG = "https://sfile.chatglm.cn/testpath/6adad3b9cc29bb9c73b3089c_0_0.jpg"

CLICK_DOWNLOAD_JS = """
() => {
  const spans = Array.from(document.querySelectorAll('.image-editor-dialog span, .el-dialog span'))
    .filter((e) => (e.innerText || '').trim() === '下载图片');
  if (!spans.length) return null;
  const btn = spans[0].closest('div');
  btn.click();
  return { cls: String(btn.className || ''), html: btn.outerHTML.slice(0, 400) };
}
"""

TOGGLE_WM_JS = """
() => {
  const el = Array.from(document.querySelectorAll('.prompt-item'))
    .find((e) => (e.innerText || '').includes('去水印'));
  if (!el) return { error: 'no toggle' };
  const label = (el.innerText || '').trim();
  el.click();
  return { label };
}
"""

CLICK_WM_ON_JS = """
() => {
  const pop = Array.from(document.querySelectorAll('.draw_options_popover'))
    .find((e) => e.getBoundingClientRect().width > 0);
  if (!pop) return { error: 'no popover' };
  const html = pop.outerHTML.slice(0, 3000);
  const on = Array.from(pop.querySelectorAll('div,span,label,button'))
    .find((e) => (e.innerText || '').trim() === '开启');
  if (on) on.click();
  return { html, clicked: !!on };
}
"""


def save(name: str, data) -> None:
    (DUMP_DIR / name).write_text(
        data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[probe8] wrote {name}", flush=True)


async def try_download(page, tag: str) -> None:
    events = {"download": None, "popup_url": None, "click": None}
    try:
        async with page.expect_download(timeout=12000) as dlctx:
            events["click"] = await page.evaluate(CLICK_DOWNLOAD_JS)
        d = await dlctx.value
        out = DUMP_DIR / f"{tag}_{d.suggested_filename}"
        await d.save_as(str(out))
        events["download"] = {"name": d.suggested_filename, "url": d.url[:300], "saved": out.name}
        print(f"[probe8] {tag}: DOWNLOAD {d.suggested_filename}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[probe8] {tag}: no download event ({exc})", flush=True)
        events["click"] = events["click"] or await page.evaluate(CLICK_DOWNLOAD_JS)
        await asyncio.sleep(2)
        urls = [p.url for p in context_pages]
        events["popup_url"] = urls
    save(f"probe8_{tag}.json", events)


async def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    global context_pages
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
        locale="zh-CN",
    ) as context:
        context_pages = list(context.pages)
        page = context.pages[0] if context.pages else await context.new_page()
        context.on("page", lambda p: context_pages.append(p))
        await page.goto(CID_URL, wait_until="domcontentloaded")
        await page.wait_for_selector(".img-answer-item img.limitSize", timeout=30000)
        await asyncio.sleep(2)

        # A: watermark toggle currently off -> download
        await page.evaluate("() => document.querySelectorAll('.img-answer-item img.limitSize')[0].click()")
        await asyncio.sleep(2.5)
        await try_download(page, "wm_off")
        await page.keyboard.press("Escape")
        await asyncio.sleep(1.5)

        # toggle 去水印 on
        save("probe8_toggle_open.json", await page.evaluate(TOGGLE_WM_JS))
        await asyncio.sleep(1.2)
        save("probe8_wm_on.json", await page.evaluate(CLICK_WM_ON_JS))
        await asyncio.sleep(1.2)
        label = await page.evaluate(
            """() => {
              const el = Array.from(document.querySelectorAll('.prompt-item'))
                .find((e) => (e.innerText || '').includes('去水印'));
              return el ? (el.innerText || '').trim() : null;
            }"""
        )
        print(f"[probe8] toggle label now: {label!r}", flush=True)

        # B: toggle on -> download again
        await page.evaluate("() => document.querySelectorAll('.img-answer-item img.limitSize')[0].click()")
        await asyncio.sleep(2.5)
        await try_download(page, "wm_on")
        await page.keyboard.press("Escape")
        print("[probe8] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
