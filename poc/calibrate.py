from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = Path(__file__).resolve().parent / "dump"

SAMPLE_JS = """
() => {
  const assistants = Array.from(document.querySelectorAll('.ds-assistant-message-main-content'));
  const last = assistants.length ? assistants[assistants.length - 1] : null;
  const userBlocks = Array.from(document.querySelectorAll('.ds-message')).filter(el =>
    el.querySelector('.ds-collapsible-text'));
  const lastUser = userBlocks.length ? userBlocks[userBlocks.length - 1] : null;
  const stopish = Array.from(document.querySelectorAll('[class*="stop"],[class*="pause"],[class*="loading"],[class*="spinner"]'))
    .map(el => String(el.className)).slice(0, 5);
  const lottie = document.querySelectorAll('.ds-lottie-toggle-icon, [class*="lottie"]').length;
  const buttons = Array.from(document.querySelectorAll('.ds-button'))
    .map(el => String(el.className)).filter(c => c.indexOf('ds-button--circle') >= 0);
  return {
    assistantCount: assistants.length,
    lastAssistantLen: last ? (last.innerText || '').length : 0,
    lastAssistant: last ? (last.innerText || '').slice(0, 120) : '',
    userCount: userBlocks.length,
    lastUser: lastUser ? (lastUser.innerText || '').slice(0, 120) : '',
    stopish: stopish,
    lottie: lottie,
    buttons: buttons,
    url: location.href,
  };
}
"""


async def main() -> None:
    DUMP_DIR.mkdir(exist_ok=True)
    profile_dir = Path("data/profile").resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(profile_dir),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://chat.deepseek.com/a/chat/", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        samples.append({"t": 0.0, **(await page.evaluate(SAMPLE_JS))})

        box = page.locator("textarea[placeholder='Message DeepSeek']").first
        await box.click()
        await page.keyboard.insert_text(
            "请写一篇不少于 600 字的散文，主题：海边日落。不要解释，不要标题，直接开始正文。"
        )
        await asyncio.sleep(0.3)
        await page.keyboard.press("Enter")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 45:
            await asyncio.sleep(0.4)
            s = await page.evaluate(SAMPLE_JS)
            s["t"] = round(time.monotonic() - t0, 2)
            samples.append(s)
            if s["t"] > 3 and s["assistantCount"] >= 1 and s["lastAssistantLen"] > 0:
                prev = samples[-2]
                if prev["lastAssistantLen"] == s["lastAssistantLen"] and s["t"] > 6:
                    break
        (DUMP_DIR / "calibration.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DUMP_DIR / "calibration_final.html").write_text(await page.content(), encoding="utf-8")
    print("samples:", len(samples))
    for s in samples:
        print(f"  t={s['t']:>6} assistants={s['assistantCount']} len={s['lastAssistantLen']:>5} users={s['userCount']}")
        for b in s["buttons"]:
            print(f"        btn: {b}")


if __name__ == "__main__":
    asyncio.run(main())
