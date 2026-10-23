"""ChatGLM calibration probe #5 (not a test).

Sends a long prompt with GLM-5.3 to observe the streaming DOM: think-status
transitions, stop control, markdown container classes. Polls fast, then dumps
the final .answer subtree.

    uv run python poc/chatglm_probe5.py
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
PROMPT = "请分十个方面详细介绍光合作用，每个方面至少150字，写成结构化长文。"
POLL_S = 1.5
MAX_WAIT_S = 160

STATE_JS = """
() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const txt = (el) => (el.innerText || '').replace(/\\s+/g, ' ').trim();
  const answers = Array.from(document.querySelectorAll('.answer'));
  const last = answers[answers.length - 1] || null;
  const status = last ? last.querySelector('.advance-thinking-status') : null;
  const wraps = last ? Array.from(last.querySelectorAll('.answer-content-wrap')).map(
      (e) => ({ cls: String(e.className||''), len: (e.innerText||'').length,
                 md: Array.from(e.querySelectorAll('[class*="markdown"],[class*="katex"],[class*="code"]')).slice(0,3).map((x)=>String(x.className||'')) })
  ) : [];
  const stop = Array.from(document.querySelectorAll('div,span,button,svg'))
    .filter((e) => visible(e) && /停止|stop生成|正在生成/.test(txt(e) || (e.getAttribute('aria-label')||'')) && e.children.length <= 2)
    .map((e) => ({ tag: e.tagName.toLowerCase(), cls: String(e.className||'').slice(0,120), text: txt(e).slice(0,40) }));
  return {
    url: location.href,
    nAnswers: answers.length,
    thinkStatus: status ? txt(status) : null,
    wraps,
    stop: stop.slice(0, 8),
    taValue: (() => { const t = document.querySelector('#search-input-box textarea'); return t ? t.value : null; })(),
    enterCls: (() => { const e = document.querySelector('.enter'); return e ? String(e.className) : null; })(),
  };
}
"""

FINAL_JS = """
() => {
  const answers = document.querySelectorAll('.answer');
  const last = answers[answers.length - 1];
  if (!last) return '';
  const clean = (s) => s.replace(/data:image\\/[^"]+/g, 'IMG');
  return clean(last.outerHTML).slice(0, 300000);
}
"""


def save(name: str, data) -> None:
    (DUMP_DIR / name).write_text(
        data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[probe5] wrote {name}", flush=True)


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
        await asyncio.sleep(3)

        trigger = await page.evaluate(
            "() => (document.querySelector('.think-mode-trigger')||{}).innerText"
        )
        print(f"[probe5] model trigger: {trigger!r}", flush=True)

        ta = page.locator("#search-input-box textarea")
        await ta.fill(PROMPT, timeout=8000)
        await asyncio.sleep(0.5)
        await page.keyboard.press("Enter")

        t0 = time.monotonic()
        states = []
        n = 0
        while time.monotonic() - t0 < MAX_WAIT_S:
            await asyncio.sleep(POLL_S)
            n += 1
            try:
                st = await page.evaluate(STATE_JS)
            except Exception as exc:  # noqa: BLE001
                print(f"[probe5] #{n} eval failed: {exc}", flush=True)
                continue
            states.append(st)
            if n % 4 == 1 or not st["stop"]:
                print(
                    f"[probe5] #{n} answers={st['nAnswers']} status={st['thinkStatus']!r} "
                    f"wraps={[(w['cls'][:40], w['len']) for w in st['wraps']]} "
                    f"stop={len(st['stop'])} enter={st['enterCls']}",
                    flush=True,
                )
            if st["nAnswers"] >= 1 and not st["stop"] and len(states) > 6:
                prev = states[-2]
                if (
                    prev
                    and prev["wraps"]
                    and st["wraps"]
                    and all(a["len"] == b["len"] for a, b in zip(prev["wraps"], st["wraps"], strict=True))
                ):
                    print("[probe5] stable, exiting loop", flush=True)
        save("probe5_states.json", states)
        save("probe5_answer.html", await page.evaluate(FINAL_JS))
        await page.screenshot(path=str(DUMP_DIR / "probe5_final.png"), full_page=False)
        print("[probe5] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
