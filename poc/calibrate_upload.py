"""Fine-grained calibration of the upload lifecycle (pending -> uploaded)."""

from __future__ import annotations

import asyncio
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from freeaiproxy.config import load_settings  # noqa: E402
from freeaiproxy.engine import selectors as S  # noqa: E402
from freeaiproxy.engine.browser_manager import BrowserManager  # noqa: E402

DUMP_DIR = Path(__file__).resolve().parent / "dump"

PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAFElEQVR4nGP8z8DAwMDAxMDAw"
    "MAAAA4AAwDxAUsAAAAASUVORK5CYII="
)

STATE_JS = """
() => {
  const imgs = Array.from(document.querySelectorAll('img[alt]')).filter(
    el => el.alt && !el.alt.includes('avatar'));
  const last = imgs.length ? imgs[imgs.length - 1] : null;
  const spinner = document.querySelector('.ds-loading');
  const spinnerNear = last
    ? (last.closest('div[role="button"], div') || last.parentElement)
    : null;
  const loadingNear = spinnerNear
    ? (spinnerNear.querySelector('.ds-loading') ? true : false)
    : false;
  const errorText = Array.from(document.querySelectorAll('[class*="error"], [class*="fail"]'))
    .map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 3);
  return {
    imgCount: imgs.length,
    alt: last ? last.alt : null,
    src: last ? (last.getAttribute('src') || '').slice(0, 30) : null,
    loadingNear: loadingNear,
    anySpinner: !!spinner,
    errorText: errorText,
  };
}
"""


async def main() -> None:
    DUMP_DIR.mkdir(exist_ok=True)
    png_path = DUMP_DIR / "sample.png"
    png_path.write_bytes(base64.b64decode(PNG_B64))
    txt_path = DUMP_DIR / "sample.txt"
    txt_path.write_text("FreeAiProxy 附件上传测试文件：包含一个数字 12345。", encoding="utf-8")
    big_path = DUMP_DIR / "sample_big.txt"
    big_path.write_text("行号内容\n" + "\n".join(f"第 {i} 行" for i in range(4000)), encoding="utf-8")

    bm = BrowserManager(load_settings())
    await bm._launch()
    page = bm.page
    assert page is not None
    try:
        for label, path in (("PNG", png_path), ("TXT", txt_path), ("BIG-TXT", big_path)):
            print(f"\n================ {label} ================")
            await page.goto(S.NEW_CHAT_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)
            t0 = time.time()
            await page.set_input_files("input[type='file']", [str(path)])
            for _ in range(40):
                await asyncio.sleep(0.25)
                st = await page.evaluate(STATE_JS)
                print(f"  t={time.time()-t0:5.2f} {st}")
                if st.get("loadingNear") is False and st.get("imgCount", 0) > 0:
                    break
                if st.get("imgCount", 0) == 0 and not st.get("anySpinner"):
                    break
            (DUMP_DIR / f"upload_{label}.html").write_text(
                await page.content(), encoding="utf-8"
            )
    finally:
        await bm.stop()


asyncio.run(main())
