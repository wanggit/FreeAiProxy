"""Probe Hy4+Expert chat and Hy3+Thinking answer/thinking DOM."""

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
HY4_PROMPT = "请只回复：收到"
THINK_PROMPT = "9.11 和 9.9 哪个大？请先简要推理，再给出结论。"

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const lastAi = Array.from(document.querySelectorAll('.agent-chat__list__item--ai')).at(-1);
  const markdowns = lastAi ? Array.from(lastAi.querySelectorAll('.hyc-common-markdown')).map(el => {
    const parents = [];
    let n = el;
    for (let i = 0; i < 7 && n; i++) {
      parents.push(String(n.className || '').slice(0, 120));
      n = n.parentElement;
    }
    return {
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 600),
      done: !!el.closest('.hyc-content-md-done'),
      visible: visible(el),
      parents,
    };
  }) : [];
  const processish = Array.from(document.querySelectorAll(
      '[class*="process"],[class*="think"],[class*="Think"],[class*="reason"],[class*="Reason"],' +
      '[class*="card-box"],[class*="CardBox"],[class*="hyc-card"]'))
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 200),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 300),
    }))
    .filter(x => x.text).slice(0, 80);
  return {
    url: location.href,
    mode: (document.querySelector("button[aria-label='Switch model']")?.innerText || '').trim(),
    items: Array.from(document.querySelectorAll('.agent-chat__list__item')).map(el => ({
      kind: el.classList.contains('agent-chat__list__item--human') ? 'human'
          : el.classList.contains('agent-chat__list__item--ai') ? 'ai' : 'other',
      outputting: el.getAttribute('data-conv-outputting'),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 800),
    })),
    markdowns,
    processish,
    bodyLen: (document.body.innerText || '').length,
    sendVisible: !!Array.from(document.querySelectorAll('[aria-label="Send"]')).find(visible),
    stopVisible: !!Array.from(document.querySelectorAll('[aria-label="Stop Answering"]')).find(visible),
  };
}
"""


async def open_menu(page) -> None:
    await page.locator("button[aria-label='Switch model']").first.click()
    await asyncio.sleep(0.6)


async def select_model(page, prefix: str) -> bool:
    await open_menu(page)
    row = page.get_by_role("menuitem", name="Select model")
    if await row.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await row.first.click()
    await asyncio.sleep(1.0)
    item = page.get_by_role("menuitemradio", name=re.compile(rf"^{prefix}"))
    if await item.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await item.first.click()
    await asyncio.sleep(1.2)
    return True


async def select_mode(page, prefix: str) -> bool:
    await open_menu(page)
    item = page.get_by_role("menuitemradio", name=re.compile(rf"^{prefix}"))
    if await item.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await item.first.click()
    await asyncio.sleep(1.0)
    return True


async def js_click(page, selector: str) -> bool:
    return bool(await page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            el.click();
            return true;
        }""",
        selector,
    ))


async def dump(page, name: str, info: dict) -> None:
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DUMP_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(DUMP_DIR / f"{name}.png"), full_page=False)


async def send_and_capture(page, prompt: str, label: str, max_s: int = 180) -> None:
    await page.locator(".ql-editor").first.click()
    await page.keyboard.insert_text(prompt)
    await asyncio.sleep(0.4)
    await page.locator("[aria-label='Send']").first.click()
    print(f"[{label}] sent", flush=True)
    t0 = time.monotonic()
    last_text = ""
    stable = 0
    while time.monotonic() - t0 < max_s:
        await asyncio.sleep(0.7)
        s = await page.evaluate(STATE_JS)
        last_ai = next((i for i in reversed(s["items"]) if i["kind"] == "ai"), None)
        text = last_ai["text"] if last_ai else ""
        if text == last_text:
            stable += 1
        else:
            stable = 0
            last_text = text
        print(f"[{label}] t={time.monotonic()-t0:5.1f} mode={s['mode']} body={s['bodyLen']:5} "
              f"md={[(m['len'], m['done'], m['visible']) for m in s['markdowns']]} "
              f"ai={text[:120]!r} send={s['sendVisible']} stop={s['stopVisible']}", flush=True)
        if text and s["sendVisible"] and not s["stopVisible"] and stable >= 5:
            break
    final = await page.evaluate(STATE_JS)
    await dump(page, f"{label}_final", final)
    print(f"[{label}] final url:", page.url, flush=True)
    if final["processish"]:
        print(f"[{label}] processish:", flush=True)
        for p in final["processish"][:20]:
            print("  ", p, flush=True)
    await page.reload(wait_until="domcontentloaded")
    await asyncio.sleep(8)
    reloaded = await page.evaluate(STATE_JS)
    await dump(page, f"{label}_reload", reloaded)
    print(f"[{label}] reload items:", reloaded["items"], flush=True)
    print(f"[{label}] reload markdowns:", reloaded["markdowns"], flush=True)
    print(f"[{label}] reload processish:", reloaded["processish"][:10], flush=True)


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

        print("[hy4] select Hy4:", await select_model(page, "Hy4 preview"), flush=True)
        print("[hy4] state:", await page.evaluate(STATE_JS), flush=True)

        await js_click(page, "button[aria-label='Add']")
        await asyncio.sleep(0.7)
        items = await page.evaluate(
            """() => Array.from(document.querySelectorAll('[role="menuitem"]'))
                .filter(el => el.getBoundingClientRect().width > 0)
                .map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim())"""
        )
        print("[hy4] expert add menu:", items, flush=True)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

        await send_and_capture(page, HY4_PROMPT, "D0_hy4")

        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.locator(".ql-editor").first.wait_for(state="visible", timeout=30000)
        await asyncio.sleep(1.0)
        print("[think] select Hy3:", await select_model(page, "Hy3"), flush=True)
        print("[think] select Thinking:", await select_mode(page, "Thinking"), flush=True)
        print("[think] state:", await page.evaluate(STATE_JS), flush=True)
        await send_and_capture(page, THINK_PROMPT, "D1_thinking", max_s=240)


if __name__ == "__main__":
    asyncio.run(main())
