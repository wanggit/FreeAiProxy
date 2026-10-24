"""Doubao composer-options probe #7 (calibration aid, not a test).

Opens each composer control (模型 / 比例 / 风格 / 模板) in a skill and dumps the
popup markup so the option-list selector can be pinned down. Also dumps the
music skill's directive chips and the video skill's 时长 slider.

    uv run python poc/doubao_probe7.py image
    uv run python poc/doubao_probe7.py video
    uv run python poc/doubao_probe7.py music

Dumps land in poc/dump/doubao/<mode>/opt2/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

sys.path.insert(0, str(ROOT / "poc"))  # noqa: E402
from doubao_probe3 import START_URL, enter_skill  # noqa: E402

DUMP_ROOT = ROOT / "poc" / "dump" / "doubao"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"

POPUP_JS = """
() => {
  const popups = Array.from(document.querySelectorAll(
      '[role="menu"],[role="listbox"],[data-radix-popper-content-wrapper],[data-slot="dropdown-menu-content"]'));
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  return popups.map((p, i) => ({
    i,
    role: p.getAttribute('role') || '',
    testid: p.getAttribute('data-testid') || '',
    cls: String(p.className || '').slice(0, 140),
    items: Array.from(p.querySelectorAll('[role="menuitem"],[role="menuitemcheckbox"],[role="option"],button')).map((el) => ({
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      text: clean(el.innerText).slice(0, 40),
      checked: el.getAttribute('aria-checked') || el.getAttribute('data-checked') || '',
      testid: el.getAttribute('data-testid') || '',
      cls: String(el.className || '').slice(0, 100),
    })).slice(0, 60),
    html: clean(p.outerHTML).slice(0, 6000),
  }));
}
"""

COMPOSER_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const keys = Array.from(document.querySelectorAll(
      '[data-input-engine-actionbar-control-key]')).map((el) => ({
    key: el.getAttribute('data-input-engine-actionbar-control-key'),
    tag: el.tagName.toLowerCase(),
    slot: el.getAttribute('data-slot') || '',
    text: clean(el.innerText).slice(0, 40),
    testid: el.getAttribute('data-testid') || '',
  }));
  const sliders = Array.from(document.querySelectorAll('input[type="range"],[role="slider"]')).map((el) => ({
    tag: el.tagName.toLowerCase(),
    min: el.getAttribute('aria-valuemin') || el.min || '',
    max: el.getAttribute('aria-valuemax') || el.max || '',
    val: el.getAttribute('aria-valuenow') || el.value || '',
    text: clean(el.innerText).slice(0, 40),
  }));
  const chips = Array.from(document.querySelectorAll(
      '[data-testid="chat_input_input"] [data-slot="dropdown-menu-trigger"]')).map((el) => ({
    text: clean(el.innerText).slice(0, 40),
    cls: String(el.className || '').slice(0, 100),
    attrs: Array.from(el.attributes).map((a) => `${a.name}=${a.value}`).join(' ').slice(0, 400),
  }));
  return { keys, sliders, chips, composer: clean(document.querySelector('[data-testid="chat_input"]').outerHTML).slice(0, 5000) };
}
"""


async def _dump(page, out_dir: Path, tag: str) -> dict:  # noqa: ANN001
    info = await page.evaluate(COMPOSER_JS)
    info["popups"] = await page.evaluate(POPUP_JS)
    (out_dir / f"{tag}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[{tag}] keys={[k['key'] for k in info['keys']]} "
          f"popups={len(info['popups'])} sliders={len(info['sliders'])} "
          f"chips={[c['text'] for c in info['chips']]}", flush=True)
    for p in info["popups"]:
        print(f"   popup[{p['i']}] role={p['role']!r} items="
              f"{[it['text'] for it in p['items']][:14]}", flush=True)
    return info


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["image", "video", "music"])
    ap.add_argument("--controls", default="")
    args = ap.parse_args()

    out_dir = DUMP_ROOT / args.mode / "opt2"
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
        try:
            await page.goto(START_URL, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[opt2] goto failed: {exc}", flush=True)
        await asyncio.sleep(4)
        await enter_skill(page, args.mode)
        await asyncio.sleep(3)
        await _dump(page, out_dir, "00_base")

        if args.mode == "music":
            chips = page.locator('[data-testid="chat_input_input"] [data-slot="dropdown-menu-trigger"]')
            n = await chips.count()
            for i in range(n):
                await chips.nth(i).click(force=True)
                await asyncio.sleep(1.5)
                await _dump(page, out_dir, f"chip_{i:02d}")
                await page.keyboard.press("Escape")
                await asyncio.sleep(1)
            await _dump(page, out_dir, "99_after")

        elif args.mode == "video":
            # 视频 composer merges 比例+时长 into one control; also open 模型.
            for name, sel in (
                ("model", '[data-input-engine-actionbar-control-key="model"]'),
                ("merged", 'button:has-text("自动")'),
            ):
                loc = page.locator(sel)
                if not await loc.count():
                    print(f"[opt2] {name} control missing", flush=True)
                    continue
                try:
                    await loc.first.click(force=True)
                    await asyncio.sleep(2)
                    info = await _dump(page, out_dir, f"{name}_open")
                    slider = page.locator('input[type="range"],[role="slider"]')
                    if await slider.count():
                        info["sliderHtml"] = await slider.first.evaluate(
                            "el => el.outerHTML"
                        )
                        (out_dir / f"{name}_open.json").write_text(
                            json.dumps(info, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(1)
                except Exception as exc:  # noqa: BLE001
                    print(f"[opt2] {name} failed: {exc}", flush=True)

        else:  # image
            for name, sel in (
                ("model", '[data-input-engine-actionbar-control-key="model"]'),
                ("ratio", 'button:has(span:text("比例"))'),
                ("style", '[data-input-engine-actionbar-control-key="style"]'),
            ):
                loc = page.locator(sel)
                if not await loc.count():
                    print(f"[opt2] {name} control missing", flush=True)
                    continue
                try:
                    await loc.first.click(force=True)
                    await asyncio.sleep(2)
                    await _dump(page, out_dir, f"{name}_open")
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(1)
                except Exception as exc:  # noqa: BLE001
                    print(f"[opt2] {name} failed: {exc}", flush=True)

        await _dump(page, out_dir, "99_after")
        print("[opt2] done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
