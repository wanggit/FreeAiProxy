"""Wenxin 音乐生成 option popovers probe 5 (real clicks + text diff).

Enters 音乐生成, then for each of 选曲风 / 选音色 / AI写歌词 performs a real
Playwright click and diffs visible leaf texts before/after to capture the
popover options, their classes and whether selecting one changes the trigger.

    uv run python poc/wenxin_music_probe5.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "wenxin"
PROFILE_DIR = ROOT / "data" / "profiles" / "wenxin"
START_URL = "https://wenxin.baidu.com/"

CLICK_CAPSULE_JS = """
(label) => {
  const spans = Array.from(document.querySelectorAll('.capsule-text'));
  const span = spans.find(s => (s.innerText||'').replace(/\\s+/g,' ').trim() === label);
  if (!span) return false;
  (span.closest('.ci-panel') || span).click();
  return true;
}
"""

LEAVES_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    if (!visible(el) || el.children.length) continue;
    const t = (el.innerText || '').replace(/\\s+/g,' ').trim();
    if (!t || t.length > 30) continue;
    const r = el.getBoundingClientRect();
    out.push({t, cls: String(el.className||'').slice(0,100),
              pcls: String(el.parentElement?.className||'').slice(0,100),
              x: Math.round(r.x), y: Math.round(r.y)});
  }
  return out;
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
        await page.locator("div.ci-editor, textarea.ci-textarea").first.wait_for(
            state="visible", timeout=30000
        )
        await asyncio.sleep(1.5)
        await page.locator(".more-dropdown-trigger").first.click(timeout=8000)
        await asyncio.sleep(1.0)
        ok = await page.evaluate(CLICK_CAPSULE_JS, "音乐生成")
        print(f"[probe] entered music mode: {ok}", flush=True)
        await asyncio.sleep(1.5)

        for label, key in (("选曲风", "style"), ("选音色", "timbre"), ("AI写歌词", "lyrics")):
            before = await page.evaluate(LEAVES_JS)
            before_keys = {(x["t"], x["cls"]) for x in before}
            loc = page.locator(f".ci-tooltip:has-text('{label}')").first
            try:
                await loc.click(timeout=6000)
                print(f"[probe] real-click {label} ok", flush=True)
            except Exception as exc:
                print(f"[probe] real-click {label} failed: {exc}", flush=True)
                continue
            await asyncio.sleep(1.3)
            after = await page.evaluate(LEAVES_JS)
            new = [x for x in after if (x["t"], x["cls"]) not in before_keys]
            _save(f"m5_{key}", {"label": label, "new": new, "after_count": len(after)})
            await page.screenshot(path=str(DUMP_DIR / f"m5_{key}.png"))
            print(f"[probe] {label}: {len(new)} new leaves", flush=True)
            for x in new[:45]:
                print(f"   + {x['t']}  [{x['cls'][:48]}]", flush=True)

            # If the popover shows selectable options, click the first plausible one
            # to see how the trigger label changes afterwards.
            opts = [x for x in new if x["pcls"] and "tooltip" not in x["cls"]]
            if opts and label != "AI写歌词":
                pick = opts[len(opts) // 3]["t"]
                try:
                    await page.locator(f".ci-tooltip:has-text('{label}')").first.click(timeout=4000)
                    await asyncio.sleep(0.8)
                except Exception:
                    pass
                await asyncio.sleep(0.8)
                try:
                    await page.get_by_text(pick, exact=True).first.click(timeout=4000)
                    await asyncio.sleep(1.0)
                    state = await page.evaluate(LEAVES_JS)
                    tool = [x for x in state if "tooltip" in x["cls"] or "ci-tool" in x["cls"]]
                    _save(f"m5_{key}_picked", {"pick": pick, "tool": tool})
                    await page.screenshot(path=str(DUMP_DIR / f"m5_{key}_picked.png"))
                    print(f"[probe] {label} picked '{pick}' -> tool: {[x['t'] for x in tool][:8]}", flush=True)
                except Exception as exc:
                    print(f"[probe] {label} pick failed: {exc}", flush=True)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.6)


if __name__ == "__main__":
    asyncio.run(main())
