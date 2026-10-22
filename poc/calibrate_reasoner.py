"""Calibrate the DeepThink (reasoner) toggle on the DeepSeek web UI."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from freeaiproxy.config import load_settings  # noqa: E402
from freeaiproxy.engine import selectors as S  # noqa: E402
from freeaiproxy.engine.browser_manager import BrowserManager  # noqa: E402

DUMP_DIR = Path(__file__).resolve().parent / "dump"

TOGGLE_JS = """
() => {
  const toggles = Array.from(document.querySelectorAll('.ds-toggle-button'));
  const deep = toggles.filter(t => {
    const label = t.parentElement ? t.parentElement.textContent || '' : '';
    return label.includes('DeepThink');
  });
  const plain = toggles.filter(t => {
    const label = t.parentElement ? t.parentElement.textContent || '' : '';
    return !label.includes('DeepThink');
  });
  const desc = (el) => el ? {
    pressed: el.getAttribute('aria-pressed'),
    selected: String(el.className).includes('--selected'),
    label: el.parentElement ? (el.parentElement.textContent || '').trim().slice(0, 30) : '',
  } : null;
  return {
    total: toggles.length,
    deep: desc(deep[0]),
    plain: desc(plain[0]),
    url: location.href,
  };
}
"""

REPLY_JS = """
() => {
  const userNodes = Array.from(document.querySelectorAll('.ds-message'))
    .filter(el => el.querySelector('.ds-collapsible-text'));
  const lastUser = userNodes.length ? userNodes[userNodes.length - 1] : null;
  const all = Array.from(document.querySelectorAll('.ds-assistant-message-main-content'));
  const replies = lastUser
    ? all.filter(el => (lastUser.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0)
    : all;
  const reply = replies.length ? replies[replies.length - 1] : null;
  const think = Array.from(document.querySelectorAll('[class*="think"], [class*="Think"]'))
    .map(el => ({cls: String(el.className).slice(0, 120), len: (el.innerText || '').length}))
    .slice(0, 6);
  return {
    replyLen: reply ? (reply.innerText || '').length : 0,
    replyText: reply ? (reply.innerText || '').slice(0, 150) : '',
    replyCount: replies.length,
    thinkNodes: think,
    assistantClasses: all.map(el => String(el.className)).slice(0, 6),
  };
}
"""


async def main() -> None:
    DUMP_DIR.mkdir(exist_ok=True)
    settings = load_settings()
    bm = BrowserManager(settings)
    await bm._launch()
    page = bm.page
    assert page is not None
    samples: list = []
    try:
        await page.goto(S.NEW_CHAT_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2.5)
        print("initial toggles:", await page.evaluate(TOGGLE_JS))

        # Locate the DeepThink toggle and click it
        print("toggles found:", await page.locator("div.ds-toggle-button").count())
        # Click the toggle whose parent contains the DeepThink label
        clicked = await page.evaluate(
            """() => {
                const toggles = Array.from(document.querySelectorAll('.ds-toggle-button'));
                for (const t of toggles) {
                    const label = t.parentElement ? (t.parentElement.textContent || '') : '';
                    if (label.includes('DeepThink')) { t.click(); return true; }
                }
                return false;
            }"""
        )
        print("clicked DeepThink:", clicked)
        await asyncio.sleep(1.0)
        print("after click:", await page.evaluate(TOGGLE_JS))
        (DUMP_DIR / "reasoner_toggle.html").write_text(await page.content(), encoding="utf-8")

        # Send a question that benefits from reasoning
        box = page.locator(S.INPUT_BOX_CANDIDATES[0]).first
        await box.click()
        await page.keyboard.insert_text("9.11 和 9.9 哪个大？请仔细推理后给出结论。")
        await asyncio.sleep(0.3)
        await page.keyboard.press("Enter")
        t0 = time.monotonic()
        last_len = -1
        stable = 0
        while time.monotonic() - t0 < 180:
            await asyncio.sleep(1.0)
            data = await page.evaluate(REPLY_JS)
            el = round(time.monotonic() - t0, 1)
            samples.append({"t": el, **data})
            print(
                f"  t={el:>6} replyLen={data['replyLen']:>6} thinkNodes={len(data['thinkNodes'])}"
                f" reply={data['replyText'][:60]!r}"
            )
            if data["replyLen"] > 0 and data["replyLen"] == last_len:
                stable += 1
                if stable >= 4:
                    break
            else:
                stable = 0
            last_len = data["replyLen"]

        print("\nfinal:", json.dumps(samples[-1] if samples else {}, ensure_ascii=False, indent=2))
        (DUMP_DIR / "reasoner_calibration.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "reasoner_final.html").write_text(await page.content(), encoding="utf-8")
    finally:
        await bm.stop()


asyncio.run(main())
