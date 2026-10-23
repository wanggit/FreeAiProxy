"""ChatGLM calibration probe #4 (not a test).

Selects the GLM-5.3 model via the .think-mode-trigger popover, sends a short
prompt, then polls the conversation DOM every 4s for a few minutes: records
the chat URL, message container classes, thinking block, stop/send state and
final answer text. Dumps to poc/dump/chatglm/probe4_*.json.

    uv run python poc/chatglm_probe4.py
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
PROMPT = "用一个词回答：1+1等于几？不要思考过程。"
POLL_S = 4
MAX_WAIT_S = 240

STATE_JS = """
() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const txt = (el) => (el.innerText || '').replace(/\\s+/g, ' ').trim();
  const dump = (el) => ({
    tag: el.tagName.toLowerCase(),
    cls: String(el.className || '').slice(0, 200),
    text: txt(el).slice(0, 200),
    aria: el.getAttribute('aria-label') || '',
  });
  const conv = document.querySelector('#mainchat') || document.body;
  const groups = Array.from(conv.querySelectorAll('[class*="answer"],[class*="question"],[class*="chat-msg"],[class*="message"]'))
    .filter((e) => e.parentElement !== document.body)
    .map(dump).slice(0, 60);
  const think = Array.from(document.querySelectorAll('[class*="think"],[class*="reason"],[class*="cot"]'))
    .filter(visible).map(dump).slice(0, 20);
  const btns = Array.from(document.querySelectorAll('button,[role="button"],div,span'))
    .filter((e) => visible(e) && e.children.length <= 1 && /停|停止|stop|loading/i.test(txt(e) || e.getAttribute('aria-label') || ''))
    .map(dump).slice(0, 20);
  const enter = document.querySelector('.enter');
  return {
    url: location.href,
    textarea: (() => { const t = document.querySelector('#search-input-box textarea'); return t ? { value: t.value, visible: visible(t) } : null; })(),
    enterCls: enter ? String(enter.className) : null,
    enterVisible: visible(enter),
    thinkTrigger: (() => { const t = document.querySelector('.think-mode-trigger'); return t ? txt(t) : null; })(),
    groups,
    think,
    stopLike: btns,
    bodyLen: (document.body.innerText || '').length,
  };
}
"""

ANSWER_HTML_JS = """
() => {
  const conv = document.querySelector('#mainchat');
  if (!conv) return '';
  return conv.innerHTML.slice(0, 400000);
}
"""


def save(name: str, data) -> None:
    (DUMP_DIR / name).write_text(
        data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[probe4] wrote {name}", flush=True)


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
        await asyncio.sleep(4)
        save("probe4_initial.json", await page.evaluate(STATE_JS))

        # open model popover and select GLM-5.3 (evaluate-click is more robust
        # here than Playwright's hit-test click: overlay tooltips steal it)
        await page.evaluate(
            "() => document.querySelectorAll('.think-mode-trigger')[0].click()"
        )
        await asyncio.sleep(1.5)
        clicked = await page.evaluate(
            """() => {
              const items = Array.from(document.querySelectorAll('.think-mode-item'));
              const t = items.find((e) => (e.innerText || '').includes('GLM-5.3'));
              if (t) { t.click(); return t.outerHTML.slice(0, 500); }
              return null;
            }"""
        )
        if not clicked:
            print("[probe4] GLM-5.3 item not found in popover", flush=True)
        await asyncio.sleep(1.5)
        trigger_txt = await page.evaluate(
            "() => (document.querySelector('.think-mode-trigger')||{}).innerText"
        )
        print(f"[probe4] trigger after select: {trigger_txt!r}", flush=True)
        save("probe4_model_selected.json", {"trigger": trigger_txt})

        # send prompt (this site swallows Playwright's native clicks; fill()
        # focuses without clicking, so use it, with an evaluate fallback)
        ta = page.locator("#search-input-box textarea")
        try:
            await ta.fill(PROMPT, timeout=8000)
        except Exception:  # noqa: BLE001
            await page.evaluate(
                """(text) => {
                  const t = document.querySelector('#search-input-box textarea');
                  t.focus();
                  t.value = text;
                  t.dispatchEvent(new Event('input', { bubbles: true }));
                }""",
                PROMPT,
            )
        await asyncio.sleep(0.8)
        await page.evaluate(STATE_JS)
        await page.keyboard.press("Enter")
        print("[probe4] pressed Enter", flush=True)

        t0 = time.monotonic()
        n = 0
        last_states = []
        while time.monotonic() - t0 < MAX_WAIT_S:
            await asyncio.sleep(POLL_S)
            n += 1
            st = await page.evaluate(STATE_JS)
            last_states.append(st)
            print(
                f"[probe4] #{n} url={st['url']} groups={len(st['groups'])} "
                f"think={len(st['think'])} stop={len(st['stopLike'])} "
                f"enterCls={st['enterCls']}",
                flush=True,
            )
            if n in (2, 5, 10, 20, len(last_states)):
                pass
        save("probe4_states.json", last_states)
        html = await page.evaluate(ANSWER_HTML_JS)
        save("probe4_mainchat.html", html)
        await page.screenshot(path=str(DUMP_DIR / "probe4_final.png"))
        print("[probe4] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
