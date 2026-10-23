"""Wenxin (文心) browser driver.

Base chat (send + completion + session resume) is calibrated against the live
page (2026-10-23) and backs the `wenxin-writing` model, plus the 图片生成 and
音乐生成 agents.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from playwright.async_api import Page

from freeaiproxy.config import Settings
from freeaiproxy.engine import wenxin_selectors as S
from freeaiproxy.engine.browser_manager import BrowserManager
from freeaiproxy.engine.errors import (
    NotLoggedInError,
    UpstreamPageChangedError,
    UpstreamRateLimitedError,
)

if TYPE_CHECKING:
    from freeaiproxy.services.models import ModelSpec

logger = logging.getLogger("freeaiproxy.wenxin")


@dataclass
class WenxinPreState:
    human_count: int
    ai_count: int
    last_human_text: str


SNAPSHOT_JS = """
() => {
  const qs = Array.from(document.querySelectorAll('[class*="conversation-flow-question-container"]'));
  const as = Array.from(document.querySelectorAll('[class*="conversation-flow-answer-container"]'));
  const lastQ = qs.length ? qs[qs.length - 1] : null;
  return {
    qCount: qs.length,
    aCount: as.length,
    lastQText: lastQ ? (lastQ.innerText || '') : '',
  };
}
"""

ANSWER_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const as = Array.from(document.querySelectorAll('[class*="conversation-flow-answer-container"]'));
  const last = as.length ? as[as.length - 1] : null;
  let text = '';
  let loading = false;
  if (last) {
    const md = Array.from(last.querySelectorAll('.cosd-markdown')).filter(visible);
    text = md.map(el => (el.innerText || '').trim()).filter(Boolean).join('\\n\\n');
    loading = !!last.querySelector('.cosd-markdown-loading');
  }
  return {aCount: as.length, text, loading};
}
"""

CLICK_CAPSULE_JS = """
(label) => {
  const spans = Array.from(document.querySelectorAll('.capsule-text'));
  const span = spans.find(s => (s.innerText || '').replace(/\\s+/g, ' ').trim() === label);
  if (!span) return false;
  const panel = span.closest('.ci-panel') || span;
  panel.click();
  return true;
}
"""


IMAGES_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const as = Array.from(document.querySelectorAll('[class*="conversation-flow-answer-container"]'));
  const last = as.length ? as[as.length - 1] : null;
  const srcs = [];
  if (last) {
    for (const img of last.querySelectorAll('img')) {
      const src = img.currentSrc || img.src || '';
      if (!src.includes('/it/u=') || !src.includes('baidu')) continue;
      const r = img.getBoundingClientRect();
      if (!visible(img) || r.width < 80 || r.height < 80) continue;
      if (!srcs.includes(src)) srcs.push(src);
    }
  }
  const loading = last ? !!last.querySelector('.cosd-markdown-loading') : false;
  return {srcs, loading};
}
"""

# Baidu serves the generated image with a `wm=`/`wmo=` watermark parameter; the
# same URL without them returns the clean original (verified 2026-10-23).
_WM_PARAMS = re.compile(r"&w[mo]=[^&?]*")


# 音乐生成 result: the answer container first shows an "AI音乐创作中" placeholder
# inside .ai-music-player (a loading icon, no <audio>), then ~2 minutes later the
# same container gains an <audio> whose src is a signed BOS mp3 link
# (verified 2026-10-23).
MUSIC_RESULT_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const as = Array.from(document.querySelectorAll('[class*="conversation-flow-answer-container"]'));
  const last = as.length ? as[as.length - 1] : null;
  const srcs = [];
  let loading = false;
  if (last) {
    for (const p of last.querySelectorAll('.ai-music-player')) {
      if (!visible(p)) continue;
      if (p.querySelector('[class*="loading"]')) loading = true;
      for (const a of p.querySelectorAll('audio, source')) {
        const src = a.currentSrc || a.src || '';
        if (src && !src.startsWith('blob:') && !srcs.includes(src)) srcs.push(src);
      }
    }
  }
  const text = last ? (last.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200) : '';
  return {aCount: as.length, srcs, loading, text};
}
"""


class WenxinDriver:
    def __init__(self, bm: BrowserManager, settings: Settings):
        self.bm = bm
        self.settings = settings

    @property
    def page(self) -> Page:
        if self.bm.page is None:
            raise UpstreamPageChangedError("browser is not running")
        return self.bm.page

    async def goto_new_chat(self) -> None:
        await self.page.goto(S.BASE_URL, wait_until="domcontentloaded")
        await self._wait_home()

    async def goto_chat(self, chat_ref: str) -> None:
        await self.page.goto(f"{S.CHAT_BASE_URL}{chat_ref}", wait_until="domcontentloaded")
        await self._wait_home()

    async def prepare(self, spec: ModelSpec) -> None:
        if await self._login_visible():
            raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
        if spec.agent == "music":
            await self._enter_music()
            return
        label = {
            "image": S.MODE_IMAGE,
            "writing": S.MODE_WRITING,
        }.get(spec.agent or "")
        if label and not await self._click_capsule(label):
            raise UpstreamPageChangedError(f"Wenxin mode capsule '{label}' is not available")
        if label:
            try:
                await self.page.locator(S.EDITOR).first.wait_for(state="visible", timeout=10000)
            except Exception as exc:
                raise UpstreamPageChangedError(
                    f"Wenxin did not show the {label} composer"
                ) from exc

    async def snapshot(self) -> WenxinPreState:
        data = await self.page.evaluate(SNAPSHOT_JS)
        return WenxinPreState(
            human_count=data["qCount"],
            ai_count=data["aCount"],
            last_human_text=data["lastQText"],
        )

    async def send_user_message(
        self,
        text: str,
        attachments: list | None = None,
        spec: ModelSpec | None = None,
    ) -> None:
        if await self._login_visible():
            raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
        await self._rate_limit_check()
        before = (await self.page.evaluate(SNAPSHOT_JS))["qCount"]

        box = self.page.locator(S.EDITOR).first
        await box.click()
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Backspace")
        if text:
            await self.page.keyboard.insert_text(text)
        await asyncio.sleep(0.3)

        send = self.page.locator(S.SEND_BUTTON).first
        try:
            if await send.is_visible():
                await send.click(timeout=8000)
            else:
                await self.page.keyboard.press("Enter")
        except Exception:
            await self.page.keyboard.press("Enter")

        fingerprint = text.strip().splitlines()[0][:12] if text.strip() else ""
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            if await self._login_visible():
                raise NotLoggedInError("login panel appeared after sending")
            data = await self.page.evaluate(SNAPSHOT_JS)
            if data["qCount"] > before:
                return
            if fingerprint and fingerprint in (data["lastQText"] or ""):
                return
        raise UpstreamPageChangedError("the message was not accepted by the page")

    async def current_chat_id(self) -> str:
        match = S.CHAT_PATH_RE.search(self.page.url)
        if not match:
            raise UpstreamPageChangedError(f"no chat id in page URL: {self.page.url}")
        return match.group(1)

    async def reply_stream(self, pre: WenxinPreState) -> AsyncIterator[str]:
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 0.3)
        t_first = self.settings.worker.timeout_first_token_s
        t_done = self.settings.worker.timeout_completion_s
        appeared = await self._wait_new_answer(pre, t_first)
        if not appeared:
            return
        last_text = ""
        last_change = time.monotonic()
        stable = 0
        while True:
            await asyncio.sleep(poll)
            if await self._login_visible():
                raise NotLoggedInError("login panel appeared while waiting for the reply")
            data = await self.page.evaluate(ANSWER_JS)
            text = data["text"] or ""
            now = time.monotonic()
            if text and text != last_text:
                stable = 0
                last_change = now
                last_text = text
                yield text
            elif text:
                stable += 1
            if text and not data["loading"] and stable >= 2:
                break
            if text and now - last_change > t_done:
                break
            if not text and now - last_change > t_first:
                break

    async def wait_completion(self, pre: WenxinPreState) -> str:
        text = ""
        async for snap in self.reply_stream(pre):
            text = snap
        return text

    async def drain(self, pre: WenxinPreState) -> str:
        return await self.wait_completion(pre)

    async def select_image_ratio(self, ratio: str | None) -> None:
        # Wenxin's 图片生成 composer exposes no aspect-ratio control (verified
        # 2026-10-23), so a requested size is accepted and ignored.
        return None

    async def wait_images(self, pre: WenxinPreState) -> list[str]:
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 0.5)
        deadline = time.monotonic() + self.settings.worker.timeout_completion_s
        last_sig = ""
        stable = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(poll)
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
            data = await self.page.evaluate(IMAGES_JS)
            srcs = [self._watermark_free(u) for u in data["srcs"]]
            sig = "|".join(srcs)
            if srcs and sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            if srcs and not data["loading"] and stable >= 2:
                return srcs
        raise UpstreamPageChangedError("the page did not produce generated images")

    async def download_images(self, targets: list[Path]) -> list[Path | None]:
        data = await self.page.evaluate(IMAGES_JS)
        srcs = [self._watermark_free(u) for u in data["srcs"]]
        results: list[Path | None] = []
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            for i, target in enumerate(targets):
                if i >= len(srcs):
                    results.append(None)
                    continue
                try:
                    resp = await client.get(srcs[i])
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("wenxin image download failed: %s", exc)
                    results.append(None)
                    continue
                ctype = resp.headers.get("content-type", "").lower()
                if "webp" in ctype:
                    ext = ".webp"
                elif "jpeg" in ctype or "jpg" in ctype:
                    ext = ".jpg"
                else:
                    ext = ".png"
                path = target.with_suffix(ext)
                path.write_bytes(resp.content)
                results.append(path)
        return results

    @staticmethod
    def _watermark_free(url: str) -> str:
        return _WM_PARAMS.sub("", url)

    async def select_music_options(
        self,
        style: str | None = None,
        voice: str | None = None,
        custom_lyrics: bool = False,
    ) -> None:
        if custom_lyrics:
            await self._pick_directive(S.MUSIC_LYRICS_TRIGGER, S.MUSIC_LYRICS_CUSTOM)
        if style:
            await self._pick_directive(S.MUSIC_STYLE_TRIGGER, style)
        if voice:
            await self._pick_directive(S.MUSIC_VOICE_TRIGGER, voice)

    async def wait_music(self, pre: WenxinPreState) -> list[str]:
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 1.0)
        deadline = time.monotonic() + self.settings.worker.timeout_completion_s
        last_sig = ""
        stable = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(poll)
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
            data = await self.page.evaluate(MUSIC_RESULT_JS)
            srcs = data["srcs"]
            sig = "|".join(srcs)
            if srcs and sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            if srcs and not data["loading"] and stable >= 2:
                return srcs
        raise UpstreamPageChangedError("the page did not produce a generated clip in time")

    async def _enter_music(self) -> None:
        # 音乐生成 lives behind the 更多 dropdown, which ignores synthetic
        # clicks; open it with a real mouse click first (verified 2026-10-23).
        if not await self._capsule_visible(S.MODE_MUSIC):
            try:
                await self.page.locator(S.MORE_TRIGGER).first.click(timeout=8000)
            except Exception as exc:
                raise UpstreamPageChangedError(
                    "Wenxin 更多 dropdown trigger is not available"
                ) from exc
            await asyncio.sleep(0.8)
        if not await self._click_capsule(S.MODE_MUSIC):
            raise UpstreamPageChangedError("Wenxin mode capsule 音乐生成 is not available")
        try:
            await self.page.locator(S.EDITOR).first.wait_for(state="visible", timeout=10000)
        except Exception as exc:
            raise UpstreamPageChangedError("Wenxin did not show the 音乐生成 composer") from exc

    async def _capsule_visible(self, label: str) -> bool:
        return bool(
            await self.page.evaluate(
                """(label) => Array.from(document.querySelectorAll('.capsule-text'))
                     .some(s => (s.innerText||'').replace(/\\s+/g,' ').trim() === label)""",
                label,
            )
        )

    async def _pick_directive(self, trigger: str, value: str) -> None:
        tip = self.page.locator(f"{S.MUSIC_TOOL_TIP}:has-text('{trigger}')").first
        try:
            await tip.click(timeout=6000)
        except Exception as exc:
            raise UpstreamPageChangedError(
                f"Wenxin music option '{trigger}' is not available"
            ) from exc
        await asyncio.sleep(0.8)
        try:
            await self.page.locator(f"{S.MUSIC_OPTION}:text-is('{value}')").first.click(
                timeout=5000
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError(
                f"Wenxin music choice '{value}' is not available"
            ) from exc
        await asyncio.sleep(0.8)

    async def _wait_new_answer(self, pre: WenxinPreState, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            if await self._login_visible():
                raise NotLoggedInError("login panel appeared while waiting for the reply")
            data = await self.page.evaluate(ANSWER_JS)
            if data["aCount"] > pre.ai_count and data["text"]:
                return True
        return False

    async def _click_capsule(self, label: str) -> bool:
        ok = bool(await self.page.evaluate(CLICK_CAPSULE_JS, label))
        if ok:
            await asyncio.sleep(1.0)
        return ok

    async def _login_visible(self) -> bool:
        return await self.bm.any_login_hint()

    async def _rate_limit_check(self) -> None:
        for hint in S.RATE_LIMIT_HINTS:
            if await self.bm.hint_present(hint):
                raise UpstreamRateLimitedError("Wenxin shows a rate-limit warning")

    async def _wait_home(self) -> None:
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        try:
            await self.page.locator(S.COMPOSER).first.wait_for(state="visible", timeout=30000)
        except Exception as exc:
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} shows the login panel") from exc
            raise UpstreamPageChangedError("Wenxin composer not found after navigation") from exc
        await asyncio.sleep(0.6)
