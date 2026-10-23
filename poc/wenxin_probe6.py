"""Wenxin 图片生成 result probe (calibration aid).

Enters the 图片生成 capsule, sends a prompt, then samples the answer container
for generated <img> sources, download controls and any ratio control. Writes
JSON + screenshots to poc/dump/wenxin/.

    uv run python poc/wenxin_probe6.py
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

DUMP_DIR = ROOT / "poc" / "dump" / "wenxin"
PROFILE_DIR = ROOT / "data" / "profiles" / "wenxin"
START_URL = "https://wenxin.baidu.com/"
PROMPT = "画一只戴红围巾的柴犬，水彩风格"

CLICK_CAPSULE_JS = """
(label) => {
  const spans = Array.from(document.querySelectorAll('.capsule-text'));
  const span = spans.find(s => (s.innerText||'').replace(/\\s+/g,' ').trim() === label);
  if (!span) return false;
  (span.closest('.ci-panel') || span).click();
  return true;
}
"""

IMG_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const as = Array.from(document.querySelectorAll('[class*="conversation-flow-answer-container"]'));
  const last = as.length ? as[as.length - 1] : null;
  const imgs = [];
  if (last) {
    for (const img of last.querySelectorAll('img')) {
      const r = img.getBoundingClientRect();
      imgs.push({
        src: (img.currentSrc || img.src || '').slice(0, 300),
        cls: String(img.className||'').slice(0,120),
        alt: img.getAttribute('alt')||'',
        w: Math.round(r.width), h: Math.round(r.height), vis: visible(img),
      });
    }
  }
  const dl = [];
  for (const el of document.querySelectorAll('button,[role="button"],a,span,div,i,svg,img')) {
    const blob = `${el.getAttribute('aria-label')||''} ${el.getAttribute('title')||''} ${String(el.className||'')}`;
    if (/download|下载/i.test(blob) && visible(el)) {
      const r = el.getBoundingClientRect();
      dl.push({tag: el.tagName.toLowerCase(), cls: String(el.className||'').slice(0,120),
               aria: el.getAttribute('aria-label')||'', title: el.getAttribute('title')||'',
               rect: [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]});
    }
  }
  const loading = last ? !!last.querySelector('.cosd-markdown-loading') : false;
  const ratio = Array.from(document.querySelectorAll('.capsule-text,button,[role="button"],[class*="ratio"],[class*="proportion"]'))
    .filter(el => visible(el) && /\\d+\\s*[:：]\\s*\\d+|比例|尺寸/.test((el.innerText||'').trim()))
    .map(el => ({tag: el.tagName.toLowerCase(), text:(el.innerText||'').trim().slice(0,20), cls:String(el.className||'').slice(0,120)})).slice(0,20);
  const answerText = last ? (last.innerText||'').replace(/\\s+/g,' ').trim().slice(0,300) : '';
  return {url: location.href, imgCount: imgs.length, imgs: imgs.slice(0,20), downloads: dl.slice(0,20), loading, ratio, answerText};
}
"""


def _save(name: str, data) -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def main() -> None:
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
        await page.locator("div.ci-editor, textarea.ci-textarea").first.wait_for(state="visible", timeout=30000)
        await asyncio.sleep(1.5)
        clicked = await page.evaluate(CLICK_CAPSULE_JS, "图片生成")
        print(f"[probe] capsule 图片生成 clicked={clicked}", flush=True)

        editor = page.locator("div.ci-editor").first
        try:
            await editor.wait_for(state="visible", timeout=20000)
        except Exception:
            inv = await page.evaluate(
                """() => {
                  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                     return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'; };
                  return {
                    url: location.href,
                    bodyText: (document.body.innerText||'').slice(0,1500),
                    editors: Array.from(document.querySelectorAll('div.ci-editor,textarea')).filter(vis)
                       .map(el=>({tag:el.tagName.toLowerCase(),cls:String(el.className||'').slice(0,120),
                                  ph:el.getAttribute('data-placeholder')||el.getAttribute('placeholder')||''})),
                    capsules: Array.from(document.querySelectorAll('.capsule-text')).filter(vis)
                       .map(el=>(el.innerText||'').trim()),
                  };
                }"""
            )
            _save("i_editor_missing", inv)
            print("[probe] editor missing; dumped i_editor_missing", flush=True)
            return
        await asyncio.sleep(0.8)
        _save("i_mode", await page.evaluate(IMG_JS))

        await editor.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.6)
        await page.locator(".ci-submit-button").first.click()
        print("[probe] image prompt sent", flush=True)

        t0 = time.monotonic()
        sample = 0
        while time.monotonic() - t0 < 150:
            await asyncio.sleep(5)
            sample += 1
            info = await page.evaluate(IMG_JS)
            _save(f"i_sample_{sample:02d}", info)
            print(
                f"[probe] t={int(time.monotonic()-t0)}s imgs={info['imgCount']} "
                f"loading={info['loading']} downloads={len(info['downloads'])}",
                flush=True,
            )
            big = [i for i in info["imgs"] if i["vis"] and i["w"] >= 100 and i["h"] >= 100]
            if big and not info["loading"]:
                await page.screenshot(path=str(DUMP_DIR / "i_done.png"))
                _save("i_done", info)
                print("[probe] images present; saved i_done", flush=True)
                break


if __name__ == "__main__":
    asyncio.run(main())
