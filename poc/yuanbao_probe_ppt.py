"""Generate a small PPT via Yuanbao Presentation Maker and inspect output."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
START_URL = "https://yuanbao.tencent.com/"
PROMPT = "制作一份关于“AI 助手发展趋势”的 3 页 PPT，简洁商务风格。"

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const markdowns = Array.from(document.querySelectorAll('.agent-chat__list__item--ai .hyc-common-markdown'))
    .filter(visible)
    .map(el => ({
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 600),
      done: !!el.closest('.hyc-content-md-done'),
      cot: !!el.closest('[class*="cot"],[class*="think"]'),
    }));
  const links = Array.from(document.querySelectorAll('a[href]')).filter(visible)
    .map(a => ({
      text: (a.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
      href: a.href.slice(0, 500),
    }))
    .filter(x => /pptx?|pdf|download|export|docs|cos\\./i.test(x.href + ' ' + x.text))
    .slice(0, 60);
  const buttons = Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible)
    .map(el => ({
      text: (el.innerText || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 100),
      aria: el.getAttribute('aria-label') || '',
      cls: String(el.className || '').slice(0, 160),
    }))
    .filter(x => /download|export|share|save|ppt|presentation|编辑|下载|导出|分享/i.test(x.text + ' ' + x.aria))
    .slice(0, 80);
  const imgs = Array.from(document.querySelectorAll('img')).filter(visible)
    .map(el => ({
      alt: el.alt || '',
      src: (el.currentSrc || el.src || '').slice(0, 300),
      w: Math.round(el.getBoundingClientRect().width),
      h: Math.round(el.getBoundingClientRect().height),
    }))
    .filter(x => x.w >= 120 && x.h >= 80).slice(0, 40);
  return {
    url: location.href,
    bodyLen: (document.body.innerText || '').length,
    bodyTail: (document.body.innerText || '').slice(-3000),
    markdowns,
    links,
    buttons,
    imgs,
    iframes: Array.from(document.querySelectorAll('iframe')).filter(visible)
      .map(el => (el.src || '').slice(0, 200)),
    ai: Array.from(document.querySelectorAll('.agent-chat__list__item--ai')).map(el => ({
      outputting: el.getAttribute('data-conv-outputting'),
      textLen: (el.innerText || '').trim().length,
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 500),
    })),
    sendVisible: !!Array.from(document.querySelectorAll('[aria-label="Send"]')).find(visible),
    stopVisible: !!Array.from(document.querySelectorAll('[aria-label="Stop Answering"]')).find(visible),
    doneCount: document.querySelectorAll('.hyc-content-md-done').length,
  };
}
"""


async def open_menu(page) -> None:
    await page.locator("button[aria-label='Switch model']").first.click()
    await asyncio.sleep(0.7)


async def select_hy4(page) -> bool:
    await open_menu(page)
    row = page.get_by_role("menuitem", name="Select model")
    if await row.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await row.first.click()
    await asyncio.sleep(1.0)
    item = page.get_by_role("menuitemradio", name=re.compile(r"^Hy4 preview"))
    if await item.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await item.first.click()
    await asyncio.sleep(1.2)
    return True


async def main() -> None:
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.locator(".ql-editor").first.wait_for(state="visible", timeout=30000)
        await asyncio.sleep(1.2)
        await select_hy4(page)

        await page.evaluate(
            """() => {
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none';
                };
                const els = Array.from(document.querySelectorAll('button,[role="button"],div,span'))
                    .filter(visible)
                    .filter(el => (el.innerText || '').replace(/\\s+/g, ' ').trim() === 'Presentation Maker');
                if (els.length) els[els.length - 1].click();
            }"""
        )
        await asyncio.sleep(2.0)

        await page.locator(".ql-editor").first.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.5)
        await page.locator("[aria-label='Send']").first.click()
        print("[ppt-gen] sent", flush=True)

        t0 = time.monotonic()
        samples = []
        last_sig = ""
        stable = 0
        while time.monotonic() - t0 < 300:
            await asyncio.sleep(1.0)
            s = await page.evaluate(STATE_JS)
            s["t"] = round(time.monotonic() - t0, 1)
            samples.append(s)
            sig = f"{s['bodyLen']}|{len(s['markdowns'])}|{len(s['links'])}|{len(s['imgs'])}|{len(s['buttons'])}"
            if sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            print(f"[ppt-gen] t={s['t']:5.1f} body={s['bodyLen']:5} "
                  f"md={[(m['len'], m['done']) for m in s['markdowns']]} "
                  f"links={len(s['links'])} imgs={len(s['imgs'])} buttons={len(s['buttons'])} "
                  f"ai={[(a['outputting'], a['textLen']) for a in s['ai']]} "
                  f"send={s['sendVisible']} stop={s['stopVisible']}", flush=True)
            if s["t"] > 10 and stable >= 10 and s["sendVisible"] and not s["stopVisible"]:
                break

        final = await page.evaluate(STATE_JS)
        (DUMP_DIR / "G0_ppt_samples.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "G0_ppt_final.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "G0_ppt_final.html").write_text(
            await page.content(), encoding="utf-8"
        )
        await page.screenshot(path=str(DUMP_DIR / "G0_ppt_final.png"))
        print("[ppt-gen] final url:", final["url"], flush=True)
        print("[ppt-gen] links:", flush=True)
        for link in final["links"]:
            print("  ", link, flush=True)
        print("[ppt-gen] buttons:", flush=True)
        for b in final["buttons"]:
            print("  ", b, flush=True)
        print("[ppt-gen] imgs:", flush=True)
        for i in final["imgs"]:
            print("  ", i, flush=True)
        print("[ppt-gen] ai:", final["ai"], flush=True)
        print("[ppt-gen] body tail:", final["bodyTail"], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
