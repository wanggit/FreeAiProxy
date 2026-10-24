"""Doubao image send probe #15 (calibration aid, not a test).

Replays the gateway's exact image path — enter skill, set ratio + style, send
— and dumps the DOM whenever the login gate misfires after sending.

    uv run python poc/doubao_probe15.py

Dumps land in poc/dump/doubao/send/.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"

SNAPSHOT_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const users = document.querySelectorAll('[data-testid="send_message"]').length;
  const ais = document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]').length;
  const loginBtn = document.querySelector('[data-testid="to_login_button"]');
  const editor = document.querySelector(
      '[data-testid="chat_input_input"] [contenteditable="true"]');
  let menu = null;
  const m = document.querySelector('[role="menu"][data-state="open"]');
  if (m) menu = clean(m.innerText).slice(0, 200);
  return {
    url: location.href, users, ais,
    loginVisible: !!(loginBtn && (loginBtn.offsetWidth || loginBtn.offsetHeight)),
    loginCount: document.querySelectorAll('[data-testid="to_login_button"]').length,
    editorCount: document.querySelectorAll(
        '[data-testid="chat_input_input"] [contenteditable="true"]').length,
    openMenu: menu,
    bodyStart: clean(document.body.innerText).slice(0, 300),
  };
}
"""


async def main() -> None:
    out_dir = DUMP_ROOT / "send"
    out_dir.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("HEADLESS") == "1"

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        humanize=True,
        os="macos",
        locale="zh-CN",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.doubao.com/chat/", wait_until="domcontentloaded")
        await asyncio.sleep(6)
        (out_dir / "00_home.html").write_text(await page.content(), encoding="utf-8")
        print("[send] start:", json.dumps(await page.evaluate(SNAPSHOT_JS),
                                          ensure_ascii=False), flush=True)

        # enter image skill
        await page.locator('[data-testid="skill_bar_button_3"]').first.click(force=True)
        await asyncio.sleep(3)

        # ratio
        await page.locator('button:has(span:text("比例"))').first.click(timeout=8000)
        await asyncio.sleep(1.5)
        item = page.locator('[role="menu"] button').filter(has_text="1:1")
        print("[send] ratio items:", await item.count(), flush=True)
        await item.first.click(force=True)
        await asyncio.sleep(1)
        # style
        style_ctrl = page.locator(
            '[data-input-engine-actionbar-control-key="style"]')
        await style_ctrl.first.click(timeout=8000)
        await asyncio.sleep(1.5)
        sitem = page.locator('[role="menu"] [role="menuitem"], [role="menu"] button') \
            .filter(has_text="油画")
        print("[send] style items:", await sitem.count(), flush=True)
        await sitem.first.click(force=True)
        await asyncio.sleep(1)

        state = await page.evaluate(SNAPSHOT_JS)
        print("[send] after options:", json.dumps(state, ensure_ascii=False), flush=True)

        editor = page.locator(
            '[data-testid="chat_input_input"] [contenteditable="true"]').first
        await editor.click()
        await page.keyboard.insert_text("a single red apple on a white table")
        await asyncio.sleep(0.5)
        await page.locator('[data-testid="chat_input_send_button"]').first.click()

        t = 0
        while t < 30:
            await asyncio.sleep(1)
            t += 1
            state = await page.evaluate(SNAPSHOT_JS)
            if state["users"] > 0 or state["loginVisible"] or state["loginCount"]:
                break
        print("[send] after send:", json.dumps(state, ensure_ascii=False), flush=True)
        (out_dir / "01_after.html").write_text(await page.content(), encoding="utf-8")
        if state["loginVisible"] or state["loginCount"]:
            print("[send] LOGIN GATE FIRED", flush=True)
        # keep waiting for the answer
        for _ in range(20):
            await asyncio.sleep(3)
            state = await page.evaluate(SNAPSHOT_JS)
            print(f"[send] users={state['users']} ais={state['ais']} "
                  f"login={state['loginCount']} body={state['bodyStart'][:120]!r}",
                  flush=True)
        print("[send] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
