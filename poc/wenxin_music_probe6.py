"""Wenxin 音乐生成 end-to-end probe (calibration aid, part 6).

Enters 音乐生成, selects 曲风=摇滚 and 音色=深情男声 while the popovers are
open, dumps how the triggers reflect the selection, switches 生成方式 to
自定义歌词 and dumps the composer, then sends a generation and polls the
answer card for <audio> sources / download controls until a clip appears.

    uv run python poc/wenxin_music_probe6.py
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
LYRICS = (
    "[主歌]\n城市醒来天还没全亮\n背包里装着昨天的伤\n地铁穿过人海的浪\n我哼着歌不慌张\n"
    "[副歌]\n走吧走吧 迎着风吧\n就算雨大 也有彩霞\n唱吧唱吧 大声唱吧\n这首歌送给长大的我们啊"
)

CLICK_CAPSULE_JS = """
(label) => {
  const spans = Array.from(document.querySelectorAll('.capsule-text'));
  const span = spans.find(s => (s.innerText||'').replace(/\\s+/g,' ').trim() === label);
  if (!span) return false;
  (span.closest('.ci-panel') || span).click();
  return true;
}
"""

TOOL_STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const tips = Array.from(document.querySelectorAll('.ci-tooltip')).filter(visible)
    .map(el => ({text:(el.innerText||'').replace(/\\s+/g,' ').trim(),
                 cls:String(el.className||'').slice(0,140),
                 html: el.outerHTML.slice(0,400)}));
  const input = document.querySelector('div.ci-editor, textarea');
  return {
    tips,
    editorCls: input ? String(input.className||'').slice(0,140) : '',
    ph: input ? (input.getAttribute('data-placeholder') || input.getAttribute('placeholder') || '') : '',
    editorText: input ? (input.innerText || input.value || '').slice(0, 200) : '',
    textareas: Array.from(document.querySelectorAll('textarea')).filter(visible)
      .map(el => ({cls:String(el.className||'').slice(0,120), ph: el.getAttribute('placeholder')||'',
                   w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)})),
  };
}
"""

ANSWER_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const as = Array.from(document.querySelectorAll('[class*="conversation-flow-answer-container"]'));
  const last = as.length ? as[as.length - 1] : null;
  const out = {aCount: as.length, audio: [], img: [], text: '', loading: false, cards: [], buttons: []};
  if (!last) return out;
  for (const a of last.querySelectorAll('audio, source')) {
    out.audio.push({tag: a.tagName.toLowerCase(), src: (a.currentSrc || a.src || '').slice(0, 400)});
  }
  for (const el of document.querySelectorAll('audio, [class*="music"], [class*="player"]')) {
    if (visible(el)) out.cards.push({tag: el.tagName.toLowerCase(), cls: String(el.className||'').slice(0,120),
      html: el.outerHTML.slice(0, 600)});
  }
  for (const el of last.querySelectorAll('button,[role="button"],span,div,i')) {
    const blob = `${el.getAttribute('aria-label')||''} ${el.getAttribute('title')||''} ${String(el.className||'')}`;
    if (/download|下载/.test(blob) && visible(el)) {
      out.buttons.push({tag: el.tagName.toLowerCase(), cls: String(el.className||'').slice(0,120),
        aria: el.getAttribute('aria-label')||'', title: el.getAttribute('title')||''});
    }
  }
  const md = Array.from(last.querySelectorAll('.cosd-markdown')).filter(visible);
  out.text = md.map(el => (el.innerText||'').trim()).filter(Boolean).join('\\n\\n').slice(0, 600);
  out.loading = !!last.querySelector('.cosd-markdown-loading');
  return out;
}
"""


def _save(name: str, data) -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _pick(page, trigger: str, option: str) -> bool:
    try:
        await page.locator(f".ci-tooltip:has-text('{trigger}')").first.click(timeout=6000)
    except Exception as exc:
        print(f"[probe] open {trigger} failed: {exc}", flush=True)
        return False
    await asyncio.sleep(1.0)
    btn = page.locator(f".music-directive-button:text-is('{option}')").first
    try:
        await btn.click(timeout=5000)
        await asyncio.sleep(1.0)
        print(f"[probe] picked {trigger} -> {option}", flush=True)
        return True
    except Exception as exc:
        print(f"[probe] pick {option} failed: {exc}", flush=True)
        await page.keyboard.press("Escape")
        return False


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
        await page.locator("div.ci-editor, textarea.ci-textarea").first.wait_for(
            state="visible", timeout=30000
        )
        await asyncio.sleep(1.5)
        await page.locator(".more-dropdown-trigger").first.click(timeout=8000)
        await asyncio.sleep(1.0)
        print(f"[probe] entered music: {await page.evaluate(CLICK_CAPSULE_JS, '音乐生成')}", flush=True)
        await asyncio.sleep(1.5)

        _save("m6_tools_initial", await page.evaluate(TOOL_STATE_JS))

        await _pick(page, "选曲风", "摇滚")
        _save("m6_after_style", await page.evaluate(TOOL_STATE_JS))
        await _pick(page, "选音色", "深情男声")
        _save("m6_after_timbre", await page.evaluate(TOOL_STATE_JS))
        await page.screenshot(path=str(DUMP_DIR / "m6_selected.png"))

        await _pick(page, "AI写歌词", "自定义歌词")
        st = await page.evaluate(TOOL_STATE_JS)
        _save("m6_after_lyrics_mode", st)
        await page.screenshot(path=str(DUMP_DIR / "m6_custom_lyrics.png"))
        print(f"[probe] custom-lyrics state: ph={st['ph']!r} editorCls={st['editorCls'][:60]}", flush=True)
        print(f"[probe] textareas: {st['textareas']}", flush=True)

        # Type lyrics into whichever editor is live, then send.
        box = page.locator("div.ci-editor").first
        try:
            await box.click(timeout=5000)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.insert_text(LYRICS)
        except Exception as exc:
            print(f"[probe] editor type failed: {exc}", flush=True)
        await asyncio.sleep(0.8)
        _save("m6_typed", await page.evaluate(TOOL_STATE_JS))
        try:
            await page.locator(".ci-submit-button").first.click(timeout=6000)
            print("[probe] sent", flush=True)
        except Exception as exc:
            print(f"[probe] send failed: {exc}", flush=True)
            return

        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < 300:
            await asyncio.sleep(6)
            n += 1
            info = await page.evaluate(ANSWER_JS)
            _save(f"m6_s{n:02d}", info)
            print(
                f"[probe] t={int(time.monotonic()-t0)}s a={info['aCount']} audio={len(info['audio'])} "
                f"cards={len(info['cards'])} btns={len(info['buttons'])} loading={info['loading']} "
                f"text={info['text'][:60]!r}",
                flush=True,
            )
            if info["audio"] or (info["cards"] and not info["loading"]):
                await page.screenshot(path=str(DUMP_DIR / "m6_done.png"))
                _save("m6_done", info)
                print("[probe] result present", flush=True)
                break
        await page.screenshot(path=str(DUMP_DIR / "m6_end.png"))


if __name__ == "__main__":
    asyncio.run(main())
