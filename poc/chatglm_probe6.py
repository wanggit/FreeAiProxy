"""ChatGLM calibration probe #6 (not a test) — AI画图.

Enters the AI画图 surface from the left nav, dumps its composer, generates one
image, then polls: records result <img> srcs (watermark params?), hover
download controls and the final page state. Dumps poc/dump/chatglm/probe6_*.

    uv run python poc/chatglm_probe6.py
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
START_URL = "https://chatglm.cn/main/alltoolsdetail?lang=zh"
PROMPT = "一只戴着宇航员头盔的橘猫，漂浮在星空里，数字绘画"
POLL_S = 4
MAX_WAIT_S = 180

INVENTORY_JS = """
() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  };
  const txt = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const dump = (el) => ({
    tag: el.tagName.toLowerCase(),
    cls: String(el.className || '').slice(0, 160),
    text: txt(el).slice(0, 80),
    aria: el.getAttribute('aria-label') || '',
    src: (el.src || '').slice(0, 300),
    w: Math.round(el.getBoundingClientRect().width),
    h: Math.round(el.getBoundingClientRect().height),
  });
  return {
    url: location.href,
    title: document.title,
    clickables: Array.from(document.querySelectorAll('button,[role="button"],[role="menuitem"],[role="tab"],a,div,span'))
      .filter((e) => visible(e) && e.children.length <= 1 && txt(e))
      .map(dump).slice(0, 200),
    editables: Array.from(document.querySelectorAll('textarea,input,[contenteditable="true"],[role="textbox"]'))
      .filter(visible).map(dump).slice(0, 30),
    imgs: Array.from(document.querySelectorAll('img'))
      .filter((e) => visible(e) && e.getBoundingClientRect().width >= 120)
      .map(dump).slice(0, 30),
    bodyText: (document.body.innerText || '').slice(0, 4000),
  };
}
"""

IMG_JS = """
() => Array.from(document.querySelectorAll('img'))
  .filter((e) => {
    const r = e.getBoundingClientRect();
    return r.width >= 100 && r.height >= 100;
  })
  .map((e) => ({ src: e.src, outer: e.outerHTML.slice(0, 400), w: Math.round(e.getBoundingClientRect().width) }))
"""

NAV_CLICK_JS = """
() => {
  const el = Array.from(document.querySelectorAll('div,span,a,li'))
    .find((e) => (e.innerText || '').trim() === 'AI画图' && e.children.length <= 1);
  if (!el) return null;
  el.click();
  return String(el.className || '') + '|' + el.tagName;
}
"""


def save(name: str, data) -> None:
    (DUMP_DIR / name).write_text(
        data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[probe6] wrote {name}", flush=True)


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
        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.wait_for_selector("#search-input-box", timeout=30000)
        await asyncio.sleep(2)

        hit = await page.evaluate(NAV_CLICK_JS)
        print(f"[probe6] nav clicked: {hit!r}", flush=True)
        await asyncio.sleep(4)
        save("probe6_after_nav.json", await page.evaluate(INVENTORY_JS))
        await page.screenshot(path=str(DUMP_DIR / "probe6_nav.png"))

        # send prompt into whichever composer is present
        ta = page.locator("#search-input-box textarea")
        try:
            await ta.fill(PROMPT, timeout=5000)
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            print("[probe6] sent via main composer", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[probe6] main composer send failed: {exc}", flush=True)
            inv = await page.evaluate(INVENTORY_JS)
            print(f"[probe6] editables: {json.dumps(inv['editables'], ensure_ascii=False)[:1200]}", flush=True)

        t0 = time.monotonic()
        n = 0
        last = None
        while time.monotonic() - t0 < MAX_WAIT_S:
            await asyncio.sleep(POLL_S)
            n += 1
            try:
                st = await page.evaluate(INVENTORY_JS)
            except Exception as exc:  # noqa: BLE001
                print(f"[probe6] #{n} eval failed: {exc}", flush=True)
                continue
            imgs = await page.evaluate(IMG_JS)
            st["bigImgs"] = imgs
            if last is None or len(imgs) != len(last) or any(a["w"] != b["w"] for a, b in zip(imgs, last, strict=True)):
                print(f"[probe6] #{n} url={st['url']} imgs={len(imgs)}", flush=True)
            last = imgs
            save("probe6_live.json", st)
        await page.screenshot(path=str(DUMP_DIR / "probe6_final.png"))
        save("probe6_imgs.json", await page.evaluate(IMG_JS))
        (DUMP_DIR / "probe6_final.html").write_text(await page.content(), encoding="utf-8")
        print("[probe6] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
