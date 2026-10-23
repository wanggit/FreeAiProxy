"""ChatGLM calibration probe #9 (not a test) — 去水印 on + fresh generation.

The composer toolbar has 去水印（关）; the popover shows 图片去水印 关闭|开启.
Turn it on properly (dumping the popover HTML to find the real control),
generate a FRESH image with the toggle on, then download it from the editor
dialog and compare with the raw CDN url.

    uv run python poc/chatglm_probe9.py
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
DRAW_URL = "https://chatglm.cn/main/gdetail/65a232c082ff90a2ad2f15e2?lang=zh"
PROMPT = "一杯冒着热气的抹茶拿铁，木质桌面，清晨阳光，摄影风格"
POLL_S = 4
MAX_WAIT_S = 180

POPOVER_HTML_JS = """
() => {
  const el = Array.from(document.querySelectorAll('.prompt-item'))
    .find((e) => (e.innerText || '').includes('去水印'));
  if (!el) return { error: 'no toggle item' };
  el.click();
  return { label: (el.innerText || '').trim() };
}
"""

POPUP_CONTENT_JS = """
() => {
  const pop = Array.from(document.querySelectorAll('.draw_options_popover'))
    .find((e) => e.getBoundingClientRect().width > 0);
  return pop ? pop.outerHTML.slice(0, 8000) : null;
}
"""

CLICK_ON_JS = """
() => {
  const pop = Array.from(document.querySelectorAll('.draw_options_popover'))
    .find((e) => e.getBoundingClientRect().width > 0);
  if (!pop) return { error: 'no popover' };
  const cands = Array.from(pop.querySelectorAll('*'))
    .filter((e) => (e.innerText || '').trim() === '开启');
  const el = cands[cands.length - 1];
  if (el) el.click();
  return { found: !!el, n: cands.length };
}
"""

LABEL_JS = """
() => {
  const el = Array.from(document.querySelectorAll('.prompt-item'))
    .find((e) => (e.innerText || '').includes('去水印'));
  return el ? (el.innerText || '').trim() : null;
}
"""

IMGS_JS = """
() => Array.from(document.querySelectorAll('.img-answer-item img.limitSize'))
  .map((e) => e.src)
"""

DL_JS = """
() => {
  const spans = Array.from(document.querySelectorAll('span'))
    .filter((e) => (e.innerText || '').trim() === '下载图片');
  if (!spans.length) return null;
  const btn = spans[0].closest('div');
  btn.click();
  return { cls: String(btn.className || '') };
}
"""


def save(name: str, data) -> None:
    (DUMP_DIR / name).write_text(
        data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[probe9] wrote {name}", flush=True)


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
        await page.goto(DRAW_URL, wait_until="domcontentloaded")
        await page.wait_for_selector("#search-input-box", timeout=30000)
        await asyncio.sleep(2)

        print(f"[probe9] before: {await page.evaluate(LABEL_JS)!r}", flush=True)
        save("probe9_open_popover.json", await page.evaluate(POPOVER_HTML_JS))
        await asyncio.sleep(1.2)
        save("probe9_popover.html", await page.evaluate(POPUP_CONTENT_JS))
        save("probe9_click_on.json", await page.evaluate(CLICK_ON_JS))
        await asyncio.sleep(1.2)
        after = await page.evaluate(LABEL_JS)
        print(f"[probe9] after toggle: {after!r}", flush=True)
        save("probe9_label.json", after)
        await page.keyboard.press("Escape")

        # fresh generation with toggle state as set
        ta = page.locator("#search-input-box textarea")
        await ta.fill(PROMPT, timeout=8000)
        await asyncio.sleep(0.5)
        await page.keyboard.press("Enter")
        print("[probe9] prompt sent", flush=True)

        t0 = time.monotonic()
        srcs: list[str] = []
        while time.monotonic() - t0 < MAX_WAIT_S:
            await asyncio.sleep(POLL_S)
            srcs = await page.evaluate(IMGS_JS)
            st = await page.evaluate(
                "() => { const e = document.querySelector('.enter'); return e ? String(e.className) : null; }"
            )
            print(f"[probe9] imgs={len(srcs)} enter={st!r}", flush=True)
            if srcs and st and "searching" not in st:
                await asyncio.sleep(2)
                srcs2 = await page.evaluate(IMGS_JS)
                if srcs2 == srcs:
                    break
        save("probe9_srcs.json", srcs)
        if not srcs:
            print("[probe9] no images, abort", flush=True)
            return

        # open editor on first image and download
        await page.evaluate(
            "() => document.querySelectorAll('.img-answer-item img.limitSize')[0].click()"
        )
        await asyncio.sleep(2.5)
        try:
            async with page.expect_download(timeout=15000) as dlctx:
                info = await page.evaluate(DL_JS)
            d = await dlctx.value
            out = DUMP_DIR / f"probe9_wm_on_{d.suggested_filename}"
            await d.save_as(str(out))
            print(f"[probe9] downloaded {out.name} via {info}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[probe9] download failed: {exc}", flush=True)
        await page.keyboard.press("Escape")
        print("[probe9] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
