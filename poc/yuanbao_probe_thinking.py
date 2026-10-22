"""Probe Hy3 + Thinking mode: selection, thinking DOM, answer DOM."""

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
PROMPT = "一个笼子里有鸡和兔共 10 只，脚共 28 只，鸡兔各几只？请给出简要推理过程。"

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
    for (let i = 0; i < 8 && n; i++) {
      parents.push(String(n.className || '').slice(0, 140));
      n = n.parentElement;
    }
    return {
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 800),
      done: !!el.closest('.hyc-content-md-done'),
      visible: visible(el),
      parents,
    };
  }) : [];
  const processish = Array.from(document.querySelectorAll(
      '[class*="process"],[class*="think"],[class*="Think"],[class*="reason"],[class*="Reason"],' +
      '[class*="cot"],[class*="CoT"],[class*="card-box"],[class*="hyc-card"]'))
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 220),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 400),
    }))
    .filter(x => x.text).slice(0, 80);
  return {
    url: location.href,
    mode: (document.querySelector("button[aria-label='Switch model']")?.innerText || '').trim(),
    items: Array.from(document.querySelectorAll('.agent-chat__list__item')).map(el => ({
      kind: el.classList.contains('agent-chat__list__item--human') ? 'human'
          : el.classList.contains('agent-chat__list__item--ai') ? 'ai' : 'other',
      outputting: el.getAttribute('data-conv-outputting'),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 900),
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
    await asyncio.sleep(0.8)


async def select_mode(page, prefix: str) -> bool:
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.4)
    await open_menu(page)
    await page.screenshot(path=str(DUMP_DIR / "E0_mode_menu.png"))
    items = await page.evaluate(
        """() => Array.from(document.querySelectorAll('[role="menuitemradio"]'))
            .filter(el => el.getBoundingClientRect().width > 0)
            .map(el => ({
              text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 100),
              checked: el.getAttribute('aria-checked'),
            }))"""
    )
    print("[thinking] mode menu items:", items, flush=True)
    item = page.get_by_role("menuitemradio", name=re.compile(rf"^{prefix}"))
    if await item.count() == 0:
        await page.keyboard.press("Escape")
        return False
    await item.first.click()
    await asyncio.sleep(1.5)
    return True


async def dump(page, name: str, info: dict) -> None:
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DUMP_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(DUMP_DIR / f"{name}.png"), full_page=False)


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

        print("[thinking] select Thinking:", await select_mode(page, "Thinking"), flush=True)
        state = await page.evaluate(STATE_JS)
        print("[thinking] state:", state, flush=True)
        await page.screenshot(path=str(DUMP_DIR / "E1_thinking_selected.png"))
        if state["mode"] != "Thinking":
            print("[thinking] mode is not Thinking, aborting", flush=True)
            return

        await page.locator(".ql-editor").first.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.4)
        await page.locator("[aria-label='Send']").first.click()
        print("[thinking] sent", flush=True)

        t0 = time.monotonic()
        last_text = ""
        stable = 0
        while time.monotonic() - t0 < 240:
            await asyncio.sleep(0.8)
            s = await page.evaluate(STATE_JS)
            last_ai = next((i for i in reversed(s["items"]) if i["kind"] == "ai"), None)
            text = last_ai["text"] if last_ai else ""
            if text == last_text:
                stable += 1
            else:
                stable = 0
                last_text = text
            print(f"[thinking] t={time.monotonic()-t0:5.1f} mode={s['mode']} body={s['bodyLen']:5} "
                  f"md={[(m['len'], m['done']) for m in s['markdowns']]} "
                  f"ai={text[:160]!r} send={s['sendVisible']} stop={s['stopVisible']}", flush=True)
            if text and s["sendVisible"] and not s["stopVisible"] and stable >= 5:
                break

        final = await page.evaluate(STATE_JS)
        await dump(page, "E2_thinking_final", final)
        print("[thinking] final url:", page.url, flush=True)
        for m in final["markdowns"]:
            print("[thinking] md parents:", m["parents"], flush=True)
        for p in final["processish"][:20]:
            print("[thinking] process:", p, flush=True)

        await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(8)
        reloaded = await page.evaluate(STATE_JS)
        await dump(page, "E3_thinking_reload", reloaded)
        print("[thinking] reload items:", reloaded["items"], flush=True)
        for m in reloaded["markdowns"]:
            print("[thinking] reload md:", m, flush=True)
        for p in reloaded["processish"][:20]:
            print("[thinking] reload process:", p, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
