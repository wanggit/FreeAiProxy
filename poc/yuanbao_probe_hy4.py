"""Probe Hy4 preview selection and Expert-mode chat behavior."""

from __future__ import annotations

import asyncio
import json
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
PROMPT = "请只回复：收到"

STATE_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const switchBtn = document.querySelector("button[aria-label='Switch model']");
  const modelsRow = document.querySelector("button[aria-label='Select model']");
  const checkedMode = Array.from(document.querySelectorAll('[role="menuitemradio"]'))
    .filter(el => el.getAttribute('aria-checked') === 'true' && visible(el))
    .map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim());
  const checkedModel = Array.from(document.querySelectorAll('[role="menuitemradio"]'))
    .filter(el => el.getAttribute('aria-checked') === 'true' && visible(el))
    .map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim());
  return {
    mode: switchBtn ? (switchBtn.innerText || '').trim() : null,
    modelsRow: modelsRow ? (modelsRow.innerText || '').replace(/\\s+/g, ' ').trim() : null,
    checkedMode,
    checkedModel,
    bodyText: (document.body.innerText || '').slice(0, 1500),
  };
}
"""

CHAT_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const items = Array.from(document.querySelectorAll('.agent-chat__list__item'));
  const lastAi = Array.from(document.querySelectorAll('.agent-chat__list__item--ai')).at(-1);
  const markdowns = Array.from(document.querySelectorAll('.agent-chat__list__item--ai .hyc-common-markdown'))
    .filter(visible)
    .map(el => ({
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 500),
      done: !!el.closest('.hyc-content-md-done'),
    }));
  const processish = Array.from(document.querySelectorAll(
      '[class*="process"],[class*="think"],[class*="Think"],[class*="tool"],[class*="Tool"]'))
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 160),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
    }))
    .filter(x => x.text).slice(0, 60);
  return {
    url: location.href,
    items: items.map(el => ({
      kind: el.classList.contains('agent-chat__list__item--human') ? 'human'
          : el.classList.contains('agent-chat__list__item--ai') ? 'ai' : 'other',
      outputting: el.getAttribute('data-conv-outputting'),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 600),
    })),
    lastAi: lastAi ? {
      outputting: lastAi.getAttribute('data-conv-outputting'),
      text: (lastAi.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 600),
    } : null,
    markdowns,
    processish,
    bodyLen: (document.body.innerText || '').length,
    sendVisible: !!Array.from(document.querySelectorAll('[aria-label="Send"]')).find(visible),
    stopVisible: !!Array.from(document.querySelectorAll('[aria-label="Stop Answering"]')).find(visible),
  };
}
"""


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


async def js_click_text(page, selector: str, prefix: str) -> bool:
    return bool(await page.evaluate(
        """([sel, want]) => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none';
            };
            const el = Array.from(document.querySelectorAll(sel)).find(e =>
                visible(e) && (e.innerText || '').replace(/\\s+/g, ' ').trim().startsWith(want));
            if (!el) return false;
            el.click();
            return true;
        }""",
        [selector, prefix],
    ))


async def open_menu(page) -> None:
    await js_click(page, "button[aria-label='Switch model']")
    await asyncio.sleep(0.7)


async def read_menu(page) -> dict:
    await open_menu(page)
    info = await page.evaluate(STATE_JS)
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.4)
    return info


async def select_model(page, prefix: str) -> bool:
    await open_menu(page)
    if not await js_click(page, "button[aria-label='Select model']"):
        return False
    await asyncio.sleep(0.7)
    ok = await js_click_text(page, "[role='menuitemradio']", prefix)
    await asyncio.sleep(1.2)
    return ok


async def select_mode(page, prefix: str) -> bool:
    await open_menu(page)
    ok = await js_click_text(page, "[role='menuitemradio']", prefix)
    await asyncio.sleep(1.2)
    return ok


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
        await asyncio.sleep(1.5)

        print("[hy4] initial:", await read_menu(page), flush=True)
        print("[hy4] select Hy4:", await select_model(page, "Hy4 preview"), flush=True)
        await asyncio.sleep(1.0)
        state = await page.evaluate(STATE_JS)
        print("[hy4] after model select:", state, flush=True)
        await page.screenshot(path=str(DUMP_DIR / "A0_hy4_selected.png"))

        menu = await read_menu(page)
        print("[hy4] menu state:", menu, flush=True)
        if menu.get("mode") != "Expert":
            print("[hy4] selecting Expert:", await select_mode(page, "Expert"), flush=True)
            print("[hy4] after Expert:", await page.evaluate(STATE_JS), flush=True)

        await page.locator(".ql-editor").first.click()
        await page.keyboard.insert_text(PROMPT)
        await asyncio.sleep(0.4)
        await page.locator("[aria-label='Send']").first.click()
        print("[hy4] sent", flush=True)

        t0 = time.monotonic()
        samples = []
        last_text = ""
        stable = 0
        while time.monotonic() - t0 < 180:
            await asyncio.sleep(0.7)
            s = await page.evaluate(CHAT_JS)
            s["t"] = round(time.monotonic() - t0, 1)
            samples.append(s)
            last_ai_text = s["lastAi"]["text"] if s["lastAi"] else ""
            if last_ai_text == last_text:
                stable += 1
            else:
                stable = 0
                last_text = last_ai_text
            print(f"[hy4] t={s['t']:5.1f} body={s['bodyLen']:5} "
                  f"md={[(m['len'], m['done']) for m in s['markdowns']]} "
                  f"ai={s['lastAi']} send={s['sendVisible']} stop={s['stopVisible']}", flush=True)
            if last_ai_text and s["sendVisible"] and not s["stopVisible"] and stable >= 4:
                break

        final = await page.evaluate(CHAT_JS)
        await dump(page, "A1_hy4_final", final)
        print("[hy4] final url:", page.url, flush=True)
        print("[hy4] final processish:", final["processish"], flush=True)

        await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(6)
        reloaded = await page.evaluate(CHAT_JS)
        await dump(page, "A2_hy4_reload", reloaded)
        print("[hy4] reload items:", reloaded["items"], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
