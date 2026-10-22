"""Calibrate the Tencent Yuanbao web UI: login, then dump the initial page.

This script does not touch production code. It uses its own persistent profile
(``data/profiles/yuanbao``) so the manual login survives for later probes.

Run:

    uv run python poc/yuanbao_calibrate.py

A headed browser opens. Log in manually (WeChat / QQ / phone). The script
dumps the anonymous page immediately, waits for login (auto-detect or the
marker file ``poc/dump/yuanbao/.logged_in``), then dumps the logged-in page.

Dumps (HTML / PNG / JSON inventory) go to ``poc/dump/yuanbao/``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from camoufox.async_api import AsyncCamoufox  # noqa: E402

DUMP_DIR = ROOT / "poc" / "dump" / "yuanbao"
PROFILE_DIR = ROOT / "data" / "profiles" / "yuanbao"
MARKER = DUMP_DIR / ".logged_in"
START_URL = "https://yuanbao.tencent.com/"
WAIT_SECONDS = 20 * 60

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
    text: (txt(el) || el.getAttribute('aria-label') || '').slice(0, 160),
    cls: String(el.className || '').slice(0, 240),
    role: el.getAttribute('role') || '',
    id: el.id || '',
    testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || '',
    placeholder: el.getAttribute('placeholder') || '',
    type: el.getAttribute('type') || '',
    aria: el.getAttribute('aria-label') || '',
    href: el.getAttribute('href') || '',
    contenteditable: el.getAttribute('contenteditable') || '',
  });
  const buttons = Array.from(document.querySelectorAll('button,[role="button"],a'))
    .filter(visible).map(desc).slice(0, 400);
  const editables = Array.from(document.querySelectorAll(
      'textarea,input,[contenteditable="true"],[contenteditable=""],[role="textbox"]'))
    .filter(visible).map(desc).slice(0, 60);
  const iframes = Array.from(document.querySelectorAll('iframe'))
    .filter(visible).map(el => el.src || '(srcless)').slice(0, 20);
  const loginSurfaces = Array.from(document.querySelectorAll(
      '[class*="hyc-login"], [class*="hyc-phone-login"], .agent-dialogue__tool__login'))
    .filter(visible).map(desc).slice(0, 20);
  const media = Array.from(document.querySelectorAll('img,canvas'))
    .filter(visible).map(el => ({
      tag: el.tagName.toLowerCase(),
      alt: el.getAttribute('alt') || '',
      src: (el.currentSrc || el.src || '').slice(0, 120),
      w: Math.round(el.getBoundingClientRect().width),
      h: Math.round(el.getBoundingClientRect().height),
    })).filter(m => m.w >= 60 && m.h >= 60).slice(0, 60);
  return {
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    bodyText: (document.body.innerText || '').slice(0, 6000),
    buttons,
    editables,
    iframes,
    loginSurfaces,
    media,
  };
}
"""

LOGIN_HINTS = (
    "扫码登录",
    "微信登录",
    "QQ登录",
    "手机号登录",
    "验证码登录",
    "登录后即可",
    "立即登录",
    "打开微信扫一扫",
    "not logged in",
    "please scan the qr code",
    "verification code",
    "log in with",
    "mobile number",
    "login with",
)


def looks_logged_in(inv: dict) -> bool:
    if not inv.get("editables"):
        return False
    if inv.get("loginSurfaces"):
        return False
    if any(e.get("type") == "tel" for e in inv.get("editables", [])):
        return False
    body = (inv.get("bodyText") or "").lower()
    if any(hint.lower() in body for hint in LOGIN_HINTS):
        return False
    exact_login = {"登录", "立即登录", "注册/登录", "登录/注册", "注册登录", "log in", "login"}
    if any((b.get("text") or "").strip().lower() in exact_login for b in inv.get("buttons", [])):
        return False
    blocked_iframe = any(
        token in src
        for src in inv.get("iframes", [])
        for token in ("login", "captcha", "open.weixin.qq.com", "graph.qq.com", "ptlogin")
    )
    return not blocked_iframe


async def dump(page, name: str, inv: dict) -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    (DUMP_DIR / f"{name}.json").write_text(
        json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    await page.screenshot(path=str(DUMP_DIR / f"{name}.png"), full_page=False)
    print(f"[dump] {name}: url={inv.get('url')} buttons={len(inv.get('buttons', []))} "
          f"editables={len(inv.get('editables', []))}", flush=True)


async def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    MARKER.unlink(missing_ok=True)

    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        humanize=True,
        os="windows",
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(START_URL, wait_until="domcontentloaded")
        await asyncio.sleep(8)
        initial = await page.evaluate(INVENTORY_JS)
        await dump(page, "00_initial", initial)

        print("!" * 70, flush=True)
        print("[Yuanbao校准] 请在弹出的浏览器中完成登录（微信/QQ/手机号）。", flush=True)
        print("[Yuanbao校准] 登录完成后脚本会自动检测并导出页面；若未自动继续，", flush=True)
        print(f"[Yuanbao校准] 可以创建标记文件：{MARKER}", flush=True)
        print("!" * 70, flush=True)

        deadline = time.monotonic() + WAIT_SECONDS
        stable = 0
        last_report = 0.0
        final = initial
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            pages = [p for p in context.pages if "yuanbao.tencent.com" in p.url]
            page = pages[-1] if pages else page
            try:
                final = await page.evaluate(INVENTORY_JS)
            except Exception as exc:
                print(f"[wait] page not ready: {type(exc).__name__}", flush=True)
                continue
            now = time.monotonic()
            if now - last_report >= 15:
                last_report = now
                body = (final.get("bodyText") or "").replace("\n", " ")[:120]
                print(f"[wait] url={final.get('url')} editable={len(final.get('editables', []))} "
                      f"body={body!r}", flush=True)
            marker = MARKER.exists()
            if marker:
                stable = 3
            elif looks_logged_in(final):
                stable += 1
            else:
                stable = 0
            if stable >= 3:
                await asyncio.sleep(4)
                final = await page.evaluate(INVENTORY_JS)
                await dump(page, "01_logged_in", final)
                print("[Yuanbao校准] 登录态已捕获，浏览器即将关闭。", flush=True)
                return

        await dump(page, "01_timeout", final)
        print("[Yuanbao校准] 等待登录超时，已导出当前状态。", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
