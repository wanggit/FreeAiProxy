"""ChatGLM calibration probe #7 (not a test) — image card actions + 去水印.

Reopens the generated draw conversation by cid, hovers the first result image
to reveal its action buttons (tooltip texts), opens the preview viewer, looks
for a download control (captured via expect_download), then toggles 去水印 and
records the new state.

    uv run python poc/chatglm_probe7.py
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

CARDS_JS = """
() => Array.from(document.querySelectorAll('.img-answer-item')).map((item) => {
  const img = item.querySelector('img.limitSize') || item.querySelector('img');
  const btns = Array.from(item.querySelectorAll('.repaint .btn')).map((b) => ({
    html: b.outerHTML.slice(0, 300),
  }));
  return {
    src: img ? img.src : null,
    rect: img ? (({x, y, width, height}) => ({x, y, width, height}))(img.getBoundingClientRect()) : null,
    btns,
    itemHtml: item.outerHTML.slice(0, 4000),
  };
})
"""

POPPERS_JS = """
() => Array.from(document.querySelectorAll('.el-popper'))
  .filter((e) => {
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  })
  .map((e) => ({ cls: String(e.className||'').slice(0,120), text: (e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,200) }))
"""

VIEWER_JS = """
() => {
  const hosts = Array.from(document.querySelectorAll('[class*="viewer"],[class*="preview"],[class*="dialog"],[class*="mask"],[class*="big-img"],[class*="fullscreen"]'))
    .filter((e) => {
      const r = e.getBoundingClientRect();
      const s = getComputedStyle(e);
      return r.width > 200 && r.height > 200 && s.display !== 'none' && s.visibility !== 'hidden';
    });
  const txt = (el) => (el.innerText || '').replace(/\\s+/g, ' ').trim();
  return hosts.map((e) => ({
    cls: String(e.className||'').slice(0,150),
    text: txt(e).slice(0, 400),
    btns: Array.from(e.querySelectorAll('div,span,button,svg,img'))
      .filter((b) => b.children.length <= 1)
      .map((b) => ({ tag: b.tagName.toLowerCase(), cls: String((b.className.baseVal ?? b.className) || '').slice(0,120), text: txt(b).slice(0,40), aria: b.getAttribute('aria-label')||'', title: b.getAttribute('title')||'' }))
      .filter((b) => b.text || b.aria || b.title || b.cls.includes('btn') || b.cls.includes('icon') || b.cls.includes('download'))
      .slice(0, 60),
  }));
}
"""


def save(name: str, data) -> None:
    (DUMP_DIR / name).write_text(
        data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[probe7] wrote {name}", flush=True)


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
        await page.goto(CID_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(".img-answer-item img.limitSize", timeout=30000)
        except Exception as exc:  # noqa: BLE001
            print(f"[probe7] no result imgs: {exc}", flush=True)
            save("probe7_nodata.json", await page.evaluate(POPPERS_JS))
            return
        await asyncio.sleep(2)

        cards = await page.evaluate(CARDS_JS)
        save("probe7_cards.json", cards)
        first = cards[0]
        if not first["rect"]:
            print("[probe7] no rect, abort", flush=True)
            return

        # hover the first image to reveal action buttons
        cx = first["rect"]["x"] + first["rect"]["width"] / 2
        cy = first["rect"]["y"] + first["rect"]["height"] / 2
        await page.mouse.move(cx, cy)
        await asyncio.sleep(1.2)
        save("probe7_hover_popper.json", await page.evaluate(POPPERS_JS))
        await page.screenshot(path=str(DUMP_DIR / "probe7_hover.png"))

        # hover each .btn to capture its tooltip text
        for bi in range(len(first["btns"])):
            info = await page.evaluate(
                """(bi) => {
                  const b = document.querySelectorAll('.img-answer-item')[0].querySelectorAll('.repaint .btn')[bi];
                  if (!b) return null;
                  b.scrollIntoView({block: 'center'});
                  const r = b.getBoundingClientRect();
                  return { x: r.x + r.width/2, y: r.y + r.height/2 };
                }""",
                bi,
            )
            if not info:
                continue
            await page.mouse.move(cx, cy)
            await asyncio.sleep(0.3)
            await page.mouse.move(info["x"], info["y"])
            await asyncio.sleep(1.0)
            pops = await page.evaluate(POPPERS_JS)
            print(f"[probe7] btn#{bi} tooltip: {[p['text'] for p in pops if p['text']]}", flush=True)
            save(f"probe7_btn{bi}_tooltip.json", pops)

        # click first image -> viewer
        await page.evaluate(
            "() => document.querySelectorAll('.img-answer-item img.limitSize')[0].click()"
        )
        await asyncio.sleep(2)
        viewer = await page.evaluate(VIEWER_JS)
        save("probe7_viewer.json", viewer)
        await page.screenshot(path=str(DUMP_DIR / "probe7_viewer.png"))

        # try a download capture: click any control whose text/hint says download/save image
        dl = await page.evaluate(
            """() => {
              const els = Array.from(document.querySelectorAll('div,span,button,a'))
                .filter((e) => {
                  const t = (e.innerText || '') + (e.getAttribute('aria-label') || '') + (e.getAttribute('title') || '') + String(e.className||'');
                  return /下载|download/i.test(t) && e.getBoundingClientRect().width > 0;
                });
              if (!els.length) return null;
              const r = els[0].getBoundingClientRect();
              els[0].click();
              return { tag: els[0].tagName, cls: String(els[0].className||'').slice(0,120), text: (els[0].innerText||'').slice(0,50), rect: {x:r.x,y:r.y} };
            }"""
        )
        save("probe7_dl_click.json", dl)
        if dl:
            try:
                async with page.expect_download(timeout=15000) as dlctx:
                    await page.evaluate(
                        """() => {
                          const els = Array.from(document.querySelectorAll('div,span,button,a'))
                            .filter((e) => {
                              const t = (e.innerText || '') + (e.getAttribute('aria-label') || '') + (e.getAttribute('title') || '') + String(e.className||'');
                              return /下载|download/i.test(t) && e.getBoundingClientRect().width > 0;
                            });
                          if (els.length) els[0].click();
                        }"""
                    )
                d = await dlctx.value
                out = DUMP_DIR / f"dl_{d.suggested_filename}"
                await d.save_as(str(out))
                print(f"[probe7] downloaded -> {out.name}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[probe7] download capture failed: {exc}", flush=True)
        await page.screenshot(path=str(DUMP_DIR / "probe7_after_dl.png"))

        # close viewer, toggle 去水印
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        toggle = await page.evaluate(
            """() => {
              const el = Array.from(document.querySelectorAll('.prompt-item'))
                .find((e) => (e.innerText||'').includes('去水印'));
              if (!el) return null;
              const before = (el.innerText||'').trim();
              el.click();
              return before;
            }"""
        )
        await asyncio.sleep(1.5)
        after = await page.evaluate(
            """() => {
              const el = Array.from(document.querySelectorAll('.prompt-item'))
                .find((e) => (e.innerText||'').includes('去水印'));
              return el ? (el.innerText||'').trim() : null;
            }"""
        )
        print(f"[probe7] watermark toggle: {toggle!r} -> {after!r}", flush=True)
        save("probe7_wm_toggle.json", {"before": toggle, "after": after})
        save("probe7_wm_popper.json", await page.evaluate(POPPERS_JS))
        await page.screenshot(path=str(DUMP_DIR / "probe7_wm.png"))
        print("[probe7] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
