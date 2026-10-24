"""Doubao login/home probe (calibration aid, not a test).

Opens a HEADED Doubao browser on the persisted doubao profile, then every
interval dumps the current page inventory (visible inputs/buttons + text),
an HTML snapshot and a screenshot into poc/dump/doubao/. Every interesting
JSON API response is also persisted to poc/dump/doubao/net/ so the
image/video/music config vocabularies (ratio / style / duration ...) can be
read off the network instead of guessed. Log in through the window; read the
dumps to extract selectors; stop the script once captured — the login state
persists in the profile for later stages.

    uv run python poc/doubao_probe.py            # ~15 min watch loop
    HEADLESS=1 uv run python poc/doubao_probe.py # no login, static dump only
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "doubao"
NET_DIR = DUMP_DIR / "net"
PROFILE_DIR = ROOT / "data" / "profiles" / "doubao"
START_URL = "https://www.doubao.com/chat/"
INTERVAL_S = 8
MAX_S = 15 * 60
NET_MAX_BYTES = 400_000
NET_URL_HINTS = re.compile(
    r"doubao\.com|ibytedance|byteimg|flow|skill|config|option|generat", re.I
)
NET_BODY_HINTS = re.compile(
    r'"(ratio|aspect|style|duration|resolution|quality|skill_type|config_key|'
    r'option_list|music_generation|image_generation|video_generation)"'
)

INVENTORY_JS = """
() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0
      && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  };
  const txt = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    text: (txt(el) || el.getAttribute('aria-label') || '').slice(0, 120),
    cls: String(el.className || '').slice(0, 220),
    role: el.getAttribute('role') || '',
    aria: el.getAttribute('aria-label') || '',
    title: el.getAttribute('title') || '',
    placeholder: el.getAttribute('placeholder') || '',
    type: el.getAttribute('type') || '',
    href: (el.getAttribute('href') || '').slice(0, 160),
    ce: el.getAttribute('contenteditable') || '',
    testid: el.getAttribute('data-testid') || '',
  });
  const clickables = Array.from(document.querySelectorAll(
      'button,[role="button"],[role="menuitem"],[role="menuitemcheckbox"],[role="option"],'
      + '[role="tab"],[role="switch"],a,[data-testid]'))
    .filter(visible).map(desc).slice(0, 300);
  const editables = Array.from(document.querySelectorAll(
      'textarea,input,[contenteditable="true"],[contenteditable=""],[role="textbox"]'))
    .filter(visible).map(desc).slice(0, 40);
  const media = Array.from(document.querySelectorAll('img,video,audio,source'))
    .filter(visible)
    .map((el) => ({tag: el.tagName.toLowerCase(), src: (el.currentSrc || el.src || '').slice(0, 300),
                   cls: String(el.className || '').slice(0, 160)}))
    .slice(0, 60);
  return {
    url: location.href,
    title: document.title,
    bodyText: (document.body.innerText || '').slice(0, 8000),
    clickables,
    editables,
    media,
  };
}
"""


def _safe_name(url: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:90].strip("_")
    return name or "resp"


async def _dump_net(response) -> None:  # noqa: ANN001
    try:
        url = response.url
        if not NET_URL_HINTS.search(url):
            return
        ctype = (response.headers or {}).get("content-type", "")
        if "json" not in ctype and "text" not in ctype:
            return
        body = await response.body()
    except Exception:  # noqa: BLE001
        return
    if len(body) > NET_MAX_BYTES or not body:
        return
    try:
        text = body.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return
    if not NET_BODY_HINTS.search(text):
        return
    path = NET_DIR / f"{int(time.time() * 1000)}_{_safe_name(url)}.json"
    path.write_text(text, encoding="utf-8")
    print(f"[net] {url[:140]}", flush=True)


async def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    NET_DIR.mkdir(parents=True, exist_ok=True)
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
        page.on("response", lambda r: asyncio.ensure_future(_dump_net(r)))
        try:
            await page.goto(START_URL, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            print(f"[probe] initial goto failed: {exc}", flush=True)

        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < MAX_S:
            n += 1
            try:
                info = await page.evaluate(INVENTORY_JS)
                (DUMP_DIR / "home.json").write_text(
                    json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (DUMP_DIR / "home.html").write_text(
                    await page.content(), encoding="utf-8"
                )
                await page.screenshot(path=str(DUMP_DIR / "home.png"), full_page=False)
                print(
                    f"[probe] #{n} url={info.get('url')} "
                    f"clickables={len(info.get('clickables', []))} "
                    f"editables={len(info.get('editables', []))} "
                    f"media={len(info.get('media', []))}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[probe] #{n} evaluate failed: {exc}", flush=True)
            if headless:
                break
            await asyncio.sleep(INTERVAL_S)
        print("[probe] window closed after timeout", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
