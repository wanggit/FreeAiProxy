"""Dump stable structures from existing Yuanbao conversations.

Covers plain-text answer, markdown answer, multimodal user turn, and the
search Sources panel.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
BASE = "https://yuanbao.tencent.com/chat/naQivTmsDa/"
CONVS = {
    "text": BASE + "0RRMouiUqn2",
    "search": BASE + "0RRMzaQ1QY4",
    "image": BASE + "0RRN6VS9LbU",
    "doc": BASE + "0RRN7TRkxoO",
}

STRUCT_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const items = Array.from(document.querySelectorAll('.agent-chat__list__item')).map(item => {
    const isHuman = item.classList.contains('agent-chat__list__item--human');
    const isAi = item.classList.contains('agent-chat__list__item--ai');
    const mds = Array.from(item.querySelectorAll('.hyc-common-markdown')).map(el => ({
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 500),
      done: !!el.closest('.hyc-content-md-done'),
    }));
    const texts = Array.from(item.querySelectorAll('.hyc-content-text')).map(el => ({
      len: (el.innerText || '').length,
      text: (el.innerText || '').slice(0, 500),
    }));
    return {
      kind: isHuman ? 'human' : isAi ? 'ai' : 'other',
      cls: String(item.className).slice(0, 240),
      text: (item.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 600),
      mds,
      texts,
      html: item.outerHTML.slice(0, 3000),
    };
  });
  const sourcesButtons = Array.from(document.querySelectorAll('button,[role="button"],span,div'))
    .filter(visible)
    .filter(el => (el.innerText || '').trim() === 'Sources')
    .map(el => ({tag: el.tagName.toLowerCase(), cls: String(el.className || '').slice(0, 160)}));
  return {
    url: location.href,
    title: document.title,
    items,
    sourcesButtons,
    doneCount: document.querySelectorAll('.hyc-content-md-done').length,
    bodyText: (document.body.innerText || '').slice(0, 2000),
  };
}
"""

SOURCES_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const links = Array.from(document.querySelectorAll('a[href]')).filter(visible)
    .map(a => ({
      text: (a.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
      href: a.href,
    }))
    .filter(x => x.href.startsWith('http'));
  const panels = Array.from(document.querySelectorAll('[class*="source"],[class*="reference"],[class*="citation"]'))
    .filter(visible)
    .map(el => ({
      cls: String(el.className || '').slice(0, 160),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 800),
    }))
    .filter(x => x.text).slice(0, 40);
  return {url: location.href, links, panels, bodyText: (document.body.innerText || '').slice(-4000)};
}
"""


async def dump(page, name: str, info: dict) -> None:
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DUMP_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(DUMP_DIR / f"{name}.png"), full_page=False)
    print(f"[dump] {name}: url={info.get('url')} items={len(info.get('items', []))}", flush=True)


async def js_click_text(page, text: str) -> bool:
    return bool(await page.evaluate(
        """(want) => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none';
            };
            const els = Array.from(document.querySelectorAll('button,[role="button"],span,div'))
                .filter(visible);
            const el = els.find(e => (e.innerText || '').trim() === want);
            if (!el) return false;
            el.click();
            return true;
        }""",
        text,
    ))


async def wait_last_ai(page, timeout_s: float = 20.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        state = await page.evaluate(
            """() => {
                const items = Array.from(document.querySelectorAll(
                    '.agent-chat__list__item--ai'));
                const last = items.length ? items[items.length - 1] : null;
                return {
                    n: items.length,
                    textLen: last ? (last.innerText || '').trim().length : 0,
                    done: last ? !!last.querySelector('.hyc-content-md-done') : false,
                    outputting: last ? last.getAttribute('data-conv-outputting') : null,
                };
            }"""
        )
        if state["n"] and state["textLen"] > 0 and state["done"]:
            await asyncio.sleep(1.0)
            return True
        await asyncio.sleep(0.5)
    return False


async def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        for name, url in CONVS.items():
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            ready = await wait_last_ai(page, timeout_s=25)
            print(f"[{name}] last AI ready={ready}", flush=True)
            info = await page.evaluate(STRUCT_JS)
            await dump(page, f"60_{name}_reload", info)
            for item in info["items"]:
                print(f"  {item['kind']:5} md={len(item['mds'])} txt={len(item['texts'])} "
                      f"text={item['text'][:100]!r}", flush=True)

            if name == "search":
                print("[search] open Sources:", await js_click_text(page, "Sources"), flush=True)
                await asyncio.sleep(1.5)
                sources = await page.evaluate(SOURCES_JS)
                await dump(page, "61_sources_panel", sources)
                for link in sources["links"][:20]:
                    print(f"  link: {link['text'][:60]!r} -> {link['href'][:100]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
