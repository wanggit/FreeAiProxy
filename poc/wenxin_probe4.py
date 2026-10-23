"""Wenxin answer-structure + completion-signal probe (calibration aid).

Sends a translation prompt (no web search) and dumps the last answer
container's HTML plus a time series of candidate "generating/stop" signals so
the driver can pick a completion rule. Writes to poc/dump/wenxin/.

    uv run python poc/wenxin_probe4.py
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
PROMPT = "把这句话翻译成英文：熟能生巧，拙工多怨"

STRUCT_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const txt = (el) => (el.innerText || '').replace(/\\s+/g, ' ').trim();
  const flows = Array.from(document.querySelectorAll('[class*="conversation-flow"]'))
    .map(el => ({cls: String(el.className||'').replace(/\\s+/g,' ').slice(0,120), len: txt(el).length, vis: visible(el)}))
    .slice(0, 30);
  const answers = Array.from(document.querySelectorAll('[class*="conversation-flow-answer-container"]'));
  const last = answers.length ? answers[answers.length - 1] : null;
  const signals = Array.from(document.querySelectorAll('[class]'))
    .filter(el => visible(el))
    .map(el => String(el.className||''))
    .filter(c => /stop|loading|generat|think|skeleton|stream/i.test(c));
  const signalCounts = {};
  for (const c of signals) { for (const t of c.split(/\\s+/)) if (/stop|loading|generat|think|skeleton|stream/i.test(t)) signalCounts[t]=(signalCounts[t]||0)+1; }
  // inner structure of the last answer: child class names + text lengths
  let inner = [];
  if (last) {
    for (const el of last.querySelectorAll('*')) {
      const t = txt(el);
      if (t && el.children.length <= 1) inner.push({cls: String(el.className||'').slice(0,120), len: t.length, sample: t.slice(0,80)});
      if (inner.length > 40) break;
    }
  }
  return {
    url: location.href,
    flows,
    answerCount: answers.length,
    answerLen: last ? txt(last).length : 0,
    answerHtml: last ? (last.outerHTML||'').slice(0, 24000) : '',
    signalCounts,
    inner: inner.slice(0, 40),
  };
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
        await page.locator("textarea.ci-textarea").first.wait_for(state="visible", timeout=30000)
        await asyncio.sleep(1.2)

        box = page.locator("textarea.ci-textarea").first
        await box.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.6)
        await page.locator(".ci-submit-button").first.click()
        print("[probe] sent", flush=True)

        t0 = time.monotonic()
        last_len = -1
        stable = 0
        while time.monotonic() - t0 < 90:
            await asyncio.sleep(2.5)
            info = await page.evaluate(STRUCT_JS)
            n = info["answerLen"]
            sig = info["signalCounts"]
            print(f"[probe] t={int(time.monotonic()-t0)}s answerLen={n} signals={sig}", flush=True)
            if n and n == last_len:
                stable += 1
                if stable >= 3:
                    _save("t_final", info)
                    await page.screenshot(path=str(DUMP_DIR / "t_final.png"))
                    print("[probe] stable; saved t_final", flush=True)
                    break
            else:
                stable = 0
            last_len = n
        else:
            _save("t_final", await page.evaluate(STRUCT_JS))


if __name__ == "__main__":
    asyncio.run(main())
