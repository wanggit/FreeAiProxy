"""Doubao (豆包, www.doubao.com/chat/) browser driver.

Calibrated against the live page (2026-10-23). Doubao wraps chat plus three
generation skills (图像生成 / 视频生成 / 音乐生成) in one composer; each skill
replaces the chat composer and adds option controls (model / ratio / style /
duration slider, and four music directive chips).
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
from playwright.async_api import Page, Response

from freeaiproxy.config import Settings
from freeaiproxy.engine import doubao_selectors as S
from freeaiproxy.engine.browser_manager import BrowserManager
from freeaiproxy.engine.errors import (
    NotLoggedInError,
    UpstreamPageChangedError,
    UpstreamRateLimitedError,
)

if TYPE_CHECKING:
    from freeaiproxy.services.models import ModelSpec

logger = logging.getLogger("freeaiproxy.doubao")

CHAT_PATH_RE = re.compile(r"/chat/([0-9A-Za-z]{6,})")

# While the model works, the answer area renders placeholder copy.
_LIMIT_HINTS = re.compile(r"今日.*(额度|次数).*已|生成数量已达上限|稍后再试|排队中")

# The video agent asks the user to confirm its parameters before rendering.
_CONFIRM_HINTS = re.compile(r"确认后|请确认|参数确认|确认参数")


@dataclass
class DoubaoPreState:
    user_count: int
    ai_count: int
    last_user_text: str


SNAPSHOT_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const users = Array.from(document.querySelectorAll('[data-testid="send_message"]'));
  const ais = Array.from(document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]'));
  const last = users.length ? users[users.length - 1] : null;
  return {
    userCount: users.length,
    aiCount: ais.length,
    lastUserText: last ? clean(last.innerText).slice(0, 400) : '',
  };
}
"""

ANSWER_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const ais = Array.from(document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]'));
  const last = ais.length ? ais[ais.length - 1] : null;
  let text = '';
  let streaming = false;
  let finishedImages = 0;
  let imageTotal = 0;
  let musicFinished = false;
  if (last) {
    const blocks = Array.from(last.querySelectorAll('[data-testid="message_text_content"]'))
      .filter((el) => !el.closest('[data-testid="message_thinking"]')
                   && !el.closest('[data-testid="suggest_message_list"]'));
    text = blocks.map((el) => clean(el.innerText)).filter(Boolean).join('\\n\\n');
    streaming = !!last.querySelector('[data-testid="message_text_content"][data-streaming="true"]');
    const imgs = Array.from(last.querySelectorAll('[data-testid="message_image_content"]'));
    imageTotal = imgs.length;
    finishedImages = imgs.filter((el) => el.getAttribute('data-finished') === 'true').length;
    const music = last.querySelector('[data-testid="music-generation-card-content"]');
    if (music) musicFinished = music.getAttribute('data-finished') === 'true';
  }
  return {aiCount: ais.length, text, streaming, imageTotal, finishedImages, musicFinished};
}
"""

# Generated images live on flow-imagex-sign.byteimg.com; the DOM carries both a
# thumbnail ("cthumb") and a full-resolution ("cgen") <source>/<img> for each
# picture, so results are de-duplicated by the image id and the cgen variant is
# preferred.
IMAGES_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const ais = Array.from(document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]'));
  const last = ais.length ? ais[ais.length - 1] : null;
  const byId = new Map();
  if (last) {
    for (const img of last.querySelectorAll('img, source')) {
      const src = img.currentSrc || img.src || '';
      if (!src.includes('byteimg.com') || !src.includes('rc_gen_image')) continue;
      const id = src.split('/rc_gen_image/')[1].split('~')[0];
      const full = src.includes('cgen');
      const prev = byId.get(id);
      if (!prev || (full && !prev.full)) byId.set(id, {src, full});
    }
  }
  return {
    srcs: Array.from(byId.values()).map((v) => v.src),
    imageTotal: last ? last.querySelectorAll('[data-testid="message_image_content"]').length : 0,
    finished: last
      ? Array.from(last.querySelectorAll('[data-testid="message_image_content"]'))
          .every((el) => el.getAttribute('data-finished') === 'true')
      : false,
  };
}
"""

VIDEO_JS = """
() => {
  const ais = Array.from(document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]'));
  const last = ais.length ? ais[ais.length - 1] : null;
  const card = last ? last.querySelector('[class*="block-video"]') : null;
  // The clip is only mounted after the play icon is clicked; before that the
  // card only shows a cover image.
  const srcs = [];
  if (last) {
    for (const v of last.querySelectorAll('video')) {
      const src = v.currentSrc || v.src || '';
      if (src && !src.startsWith('blob:') && !srcs.includes(src)) srcs.push(src);
    }
  }
  return {aiCount: ais.length, hasCard: !!card, srcs};
}
"""

MUSIC_JS = """
() => {
  const ais = Array.from(document.querySelectorAll(
      '[data-testid="receive_message"][data-message-role="assistant"]'));
  const last = ais.length ? ais[ais.length - 1] : null;
  const card = last ? last.querySelector('[data-testid="music-generation-card-content"]') : null;
  return {
    aiCount: ais.length,
    hasCard: !!card,
    finished: !!card && card.getAttribute('data-finished') === 'true',
  };
}
"""


class DoubaoDriver:
    def __init__(self, bm: BrowserManager, settings: Settings):
        self.bm = bm
        self.settings = settings
        self._music_custom_lyrics = False
        # Last get_video payload seen on the current page, (monotonic, mp4 url).
        # The player often reuses the response it prefetched while the card
        # rendered, so a click alone can produce no new network hit.
        self._music_media: tuple[float, str] | None = None
        self._music_listener_page: Page | None = None

    @property
    def page(self) -> Page:
        if self.bm.page is None:
            raise UpstreamPageChangedError("browser is not running")
        return self.bm.page

    # ------------------------------------------------------------- navigation

    async def goto_new_chat(self) -> None:
        self._ensure_music_listener()
        await self.page.goto(S.BASE_URL, wait_until="domcontentloaded")
        await self._wait_home()
        await self._exit_skill()

    async def goto_chat(self, chat_ref: str) -> None:
        self._ensure_music_listener()
        await self.page.goto(f"{S.BASE_URL}{chat_ref}", wait_until="domcontentloaded")
        await self._wait_home()

    async def current_chat_id(self) -> str:
        match = CHAT_PATH_RE.search(self.page.url)
        if not match:
            raise UpstreamPageChangedError(f"no chat id in page URL: {self.page.url}")
        return match.group(1)

    async def prepare(self, spec: ModelSpec) -> None:
        if await self._login_visible():
            raise NotLoggedInError(f"{self.bm.login.name} is not logged in")
        agent = spec.agent or ""
        if agent in ("image", "video", "music"):
            if agent == "music":
                # Drop any get_video payload from a previous conversation so
                # wait_music cannot reuse a stale track URL.
                self._music_media = None
            await self._enter_skill(agent)
        else:
            await self._exit_skill()
            if spec.ui_model:
                await self._select_chat_model(spec.ui_model)

    async def snapshot(self) -> DoubaoPreState:
        data = await self.page.evaluate(SNAPSHOT_JS)
        return DoubaoPreState(
            user_count=data["userCount"],
            ai_count=data["aiCount"],
            last_user_text=data["lastUserText"],
        )

    # ---------------------------------------------------------------- sending

    async def send_user_message(
        self,
        text: str,
        attachments: list | None = None,
        spec: ModelSpec | None = None,
    ) -> None:
        if await self._login_visible():
            raise NotLoggedInError(f"{self.bm.login.name} is not logged in")
        await self._rate_limit_check()
        before = (await self.page.evaluate(SNAPSHOT_JS))["userCount"]

        if spec is not None and spec.agent == "music" and not self._music_custom_lyrics:
            await self._fill_music_theme(text)
        else:
            editor = self.page.locator(S.EDITOR).first
            if not await editor.count():
                raise UpstreamPageChangedError("the Doubao composer is not available")
            await editor.click()
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Backspace")
            if text:
                await self.page.keyboard.insert_text(text)
            await asyncio.sleep(0.4)

        if attachments:
            await self._attach_files(attachments)

        await self._click_send()

        fingerprint = text.strip().splitlines()[0][:12] if text.strip() else ""
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            if await self._login_visible():
                raise NotLoggedInError("login panel appeared after sending")
            data = await self.page.evaluate(SNAPSHOT_JS)
            if data["userCount"] > before:
                return
            if fingerprint and fingerprint in (data["lastUserText"] or ""):
                return
            if self.page.url != S.BASE_URL and CHAT_PATH_RE.search(self.page.url):
                return
        raise UpstreamPageChangedError("the message was not accepted by the page")

    # ---------------------------------------------------------------- replies

    async def reply_stream(self, pre: DoubaoPreState) -> AsyncIterator[str]:
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 0.3)
        t_first = self.settings.worker.timeout_first_token_s
        t_done = self.settings.worker.timeout_completion_s
        if not await self._wait_new_answer(pre, t_first):
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
            if text and not data["streaming"] and stable >= 2:
                break
            if text and now - last_change > t_done:
                break
            if not text and now - last_change > t_first:
                break

    async def wait_completion(self, pre: DoubaoPreState) -> str:
        text = ""
        async for snap in self.reply_stream(pre):
            text = snap
        return text

    async def drain(self, pre: DoubaoPreState) -> str:
        return await self.wait_completion(pre)

    # -------------------------------------------------------- image / video

    async def select_image_ratio(self, ratio: str | None) -> None:
        target = ratio or S.IMAGE_RATIO_AUTO
        await self._pick_menu_item(S.RATIO_CONTROL, target, prefix=True)
        await self._expect_trigger_text(S.RATIO_CONTROL, target)

    async def select_image_style(self, style: str) -> None:
        await self._pick_menu_item(S.STYLE_CONTROL, style, prefix=True)
        await self._expect_trigger_text(S.STYLE_CONTROL, style)

    async def select_video_options(
        self, ratio: str | None = None, duration: int | None = None
    ) -> None:
        if ratio is not None:
            await self._pick_menu_item(
                S.VIDEO_RATIO_CONTROL, ratio or S.VIDEO_RATIO_AUTO, prefix=True
            )
            await self._expect_trigger_text(S.VIDEO_RATIO_CONTROL, ratio)
        if duration is not None:
            await self._set_video_duration(duration)
        # Never hand a still-open panel to the send path.
        await self.page.keyboard.press("Escape")
        await asyncio.sleep(0.3)

    async def wait_images(self, pre: DoubaoPreState) -> list[str]:
        return await self._wait_media(IMAGES_JS, self._completion_timeout(), "images")

    async def wait_videos(self, pre: DoubaoPreState) -> list[str]:
        """Wait for the rendered clip and return its direct MP4 URL.

        Two discoveries from the 2026-10-23 calibration (see docs/DOUBAO.md):

        1. The video skill is an agent: it first answers with a parameter
           confirmation ("确认后我再开始生成视频。") and only starts rendering
           after the user agrees, so the driver sends 确认 itself.
        2. The finished clip is not a <video> element: the assistant bubble
           shows a cover image (`.block-video`) with an empty player shell.
           Clicking the play icon makes the player fetch the real MP4, so the
           driver plays the card once and captures that response.
        """
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 2.0)
        deadline = time.monotonic() + self._completion_timeout()
        confirmed = False
        while time.monotonic() < deadline:
            await asyncio.sleep(poll)
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} is not logged in")
            data = await self.page.evaluate(VIDEO_JS)
            srcs = list(data.get("srcs") or [])
            if srcs:
                return srcs
            if data.get("hasCard"):
                found = await self._play_video_card()
                if found:
                    return found
            elif not confirmed and await self._needs_video_confirmation():
                await self._send_confirmation()
                confirmed = True
        raise UpstreamPageChangedError("the page did not produce a generated video")

    async def _play_video_card(self) -> list[str]:
        """Click the cover's play icon and capture the MP4 the player loads."""
        icon = self.page.locator(S.VIDEO_PLAY_ICON)
        if not await icon.count():
            return []
        media: list[str] = []

        def _on_response(resp: Response) -> None:
            url = resp.url
            ctype = resp.headers.get("content-type", "")
            if "video" in ctype or ".mp4" in url:
                media.append(url)

        self.page.on("response", _on_response)
        try:
            logger.info("playing the Doubao video card to capture the MP4")
            await icon.first.click(force=True, timeout=10_000)
            # The player fetches the stream right away; a few seconds is plenty.
            for _ in range(12):
                await asyncio.sleep(1.0)
                if media:
                    break
        except Exception as exc:  # noqa: BLE001 - click races with re-render
            logger.warning("doubao video card click failed: %s", exc)
        finally:
            self.page.remove_listener("response", _on_response)
        if not media:
            # Fall back to whatever the mounted player exposes.
            data = await self.page.evaluate(VIDEO_JS)
            media = list(data.get("srcs") or [])
        return media

    async def _needs_video_confirmation(self) -> bool:
        data = await self.page.evaluate(ANSWER_JS)
        text = data.get("text") or ""
        if not text or data.get("streaming"):
            return False
        return bool(_CONFIRM_HINTS.search(text))

    async def _send_confirmation(self) -> None:
        logger.info("confirming the Doubao video parameters")
        editor = self.page.locator(S.EDITOR).first
        if not await editor.count():
            raise UpstreamPageChangedError("the Doubao composer is not available")
        await editor.click()
        await self.page.keyboard.insert_text(S.VIDEO_CONFIRM_TEXT)
        await asyncio.sleep(0.4)
        await self._click_send()

    def _completion_timeout(self) -> float:
        return float(self.settings.worker.timeout_completion_s)

    async def download_images(self, targets: list[Path]) -> list[Path | None]:
        data = await self.page.evaluate(IMAGES_JS)
        srcs = data["srcs"]
        results: list[Path | None] = []
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            for i, target in enumerate(targets):
                if i >= len(srcs):
                    results.append(None)
                    continue
                try:
                    resp = await client.get(srcs[i])
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("doubao image download failed: %s", exc)
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

    # ----------------------------------------------------------------- music

    async def select_music_options(
        self,
        style: str | None = None,
        voice: str | None = None,
        custom_lyrics: bool = False,
        mood: str | None = None,
    ) -> None:
        self._music_custom_lyrics = custom_lyrics
        if custom_lyrics:
            await self._pick_chip(0, S.MUSIC_LYRIC_MODES[1])
        if style is not None:
            await self._pick_chip(1, style)
        if mood is not None:
            await self._pick_chip(2, mood)
        if voice is not None:
            await self._pick_chip(3, voice)

    async def wait_music(self, pre: DoubaoPreState) -> list[str]:
        data = await self.page.evaluate(MUSIC_JS)
        if not data["hasCard"]:
            deadline = time.monotonic() + float(self.settings.worker.timeout_first_token_s)
            while time.monotonic() < deadline and not data["hasCard"]:
                await asyncio.sleep(1.0)
                data = await self.page.evaluate(MUSIC_JS)
        if not data["hasCard"]:
            raise UpstreamPageChangedError("the page did not render a music card")
        deadline = time.monotonic() + self._completion_timeout()
        while time.monotonic() < deadline:
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} is not logged in")
            data = await self.page.evaluate(MUSIC_JS)
            if data["finished"]:
                return [await self._recover_music_url()]
            await asyncio.sleep(2.0)
        raise UpstreamPageChangedError("the music card did not finish in time")

    async def _recover_music_url(self) -> str:
        """Get the finished track's MP4 URL.

        Doubao never exposes an <audio> element: the card's player calls
        /alice/media/bigmusic/get_video, whose JSON carries a signed MP4 that
        muxes the cover art with the audio track (verified 2026-10-23).

        The player prefetches that response while the card renders, so the
        click alone may produce no new network hit — a payload captured by the
        page-level listener (see `_ensure_music_listener`) is reused instead.
        """
        play = self.page.locator(S.PLAY_BUTTON)
        if not await play.count():
            raise UpstreamPageChangedError("the music card has no play button")
        self._ensure_music_listener()
        captured: dict[str, str] = {}

        async def _on_response(resp: Response) -> None:
            if S.MUSIC_MEDIA_API not in resp.url:
                return
            try:
                body = await resp.json()
            except Exception:  # noqa: BLE001
                return
            url = (body.get("data") or {}).get("url") or ""
            if url:
                captured["url"] = url

        self.page.on("response", _on_response)
        try:
            await play.last.click(force=True, timeout=10_000)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and "url" not in captured:
                cached = self._fresh_music_media()
                if cached:
                    captured["url"] = cached
                    break
                await asyncio.sleep(1.0)
        finally:
            self.page.remove_listener("response", _on_response)
        url = captured.get("url") or ""
        if not url:
            raise UpstreamPageChangedError("music playback did not return a track")
        return url

    def _fresh_music_media(self) -> str | None:
        if self._music_media is None:
            return None
        ts, url = self._music_media
        # The prefetched URL stays valid for a while; 15 min is generous.
        return url if time.monotonic() - ts < 900 else None

    def _ensure_music_listener(self) -> None:
        """Keep a page-level get_video listener across navigations."""
        page = self.page
        if self._music_listener_page is page:
            return
        if self._music_listener_page is not None:
            with contextlib.suppress(Exception):
                self._music_listener_page.remove_listener("response", self._on_music_media)
        page.on("response", self._on_music_media)
        self._music_listener_page = page

    async def _on_music_media(self, resp: Response) -> None:
        if S.MUSIC_MEDIA_API not in resp.url:
            return
        try:
            body = await resp.json()
        except Exception:  # noqa: BLE001
            return
        url = (body.get("data") or {}).get("url") or ""
        if url:
            self._music_media = (time.monotonic(), url)

    # --------------------------------------------------------------- helpers

    async def _wait_home(self) -> None:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if await self.page.locator(S.COMPOSER).count():
                return
            await asyncio.sleep(0.5)
        raise UpstreamPageChangedError("the Doubao composer never appeared")

    async def _login_visible(self) -> bool:
        try:
            loc = self.page.locator(S.LOGIN_BUTTON)
            if await loc.count() and await loc.first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            return True
        editor = self.page.locator(S.EDITOR)
        try:
            return not await editor.count()
        except Exception:  # noqa: BLE001
            return True

    async def _rate_limit_check(self) -> None:
        body = await self.page.evaluate("() => document.body.innerText.slice(0, 4000)")
        if _LIMIT_HINTS.search(body or ""):
            raise UpstreamRateLimitedError("Doubao reports the daily quota is used up")

    async def _wait_new_answer(self, pre: DoubaoPreState, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = await self.page.evaluate(SNAPSHOT_JS)
            if data["aiCount"] > pre.ai_count:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _wait_media(self, js: str, timeout: float, label: str) -> list[str]:
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 1.0)
        deadline = time.monotonic() + timeout
        last_sig = ""
        stable = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(poll)
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} is not logged in")
            data = await self.page.evaluate(js)
            srcs = list(data.get("srcs") or [])
            sig = "|".join(srcs)
            if srcs and sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            ready = data.get("finished", True) if isinstance(data, dict) else True
            if srcs and ready and stable >= 2:
                return srcs
        raise UpstreamPageChangedError(f"the page did not produce generated {label}")

    async def _exit_skill(self) -> None:
        exit_btn = self.page.locator(S.SKILL_EXIT)
        if await exit_btn.count():
            try:
                await exit_btn.first.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to leave the active Doubao skill: %s", exc)

    async def _enter_skill(self, mode: str) -> None:
        label = S.SKILL_LABELS[mode]
        testid = {"image": S.SKILL_IMAGE, "video": S.SKILL_VIDEO, "music": S.SKILL_MUSIC}[mode]
        # The skill bar re-renders after a previous skill was exited (and after
        # a fresh navigation); poll instead of assuming it is already there.
        deadline = time.monotonic() + 15
        while True:
            current = self.page.locator(S.SKILL_EXIT)
            if await current.count():
                text = (await current.first.inner_text()).strip()
                if label in text:
                    return
                await self._exit_skill()
            loc = self.page.locator(f'[data-testid="{testid}"]')
            more = self.page.locator(f'[data-testid="{S.SKILL_MORE}"]')
            if await loc.count():
                await loc.first.click()
                break
            if await more.count():
                await more.first.click()
                await asyncio.sleep(1.5)
                item = self.page.get_by_text(label, exact=True)
                if await item.count():
                    await item.first.click()
                    break
            if time.monotonic() >= deadline:
                raise UpstreamPageChangedError(f"Doubao skill bar does not offer {label}")
            await asyncio.sleep(1.0)
        try:
            await self.page.locator(S.EDITOR).first.wait_for(state="visible", timeout=15000)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamPageChangedError(f"Doubao did not open the {label} composer") from exc
        await asyncio.sleep(1)

    async def _select_chat_model(self, model: str) -> None:
        # The chat model control is optional; keep the page default when the
        # trigger is missing so plain chat keeps working after UI shuffles.
        control = self.page.locator(S.MODEL_CONTROL)
        if not await control.count():
            return
        try:
            await control.first.click(timeout=5000)
            await self._click_menu_item(model, prefix=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("doubao chat model selection skipped: %s", exc)
            await self.page.keyboard.press("Escape")

    async def _open_control(self, selector: str) -> None:
        control = self.page.locator(selector).first
        if not await control.count():
            raise UpstreamPageChangedError(f"Doubao control {selector} is not available")
        await control.click()
        try:
            await self.page.locator(S.POPUP_MENU).first.wait_for(state="visible", timeout=8000)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamPageChangedError(f"Doubao control {selector} opened no menu") from exc

    async def _click_menu_item(self, name: str, prefix: bool = False) -> None:
        items = self.page.locator(S.POPUP_ITEM)
        # Match on the visible label; option labels may carry a trailing hint
        # ("Seedream 5.0 Flash 专业快速"), so fall back to a prefix match.
        exact = items.filter(has_text=re.compile(f"^{re.escape(name)}$"))
        if await exact.count():
            target = exact.first
        elif prefix:
            loose = items.filter(has_text=name)
            if not await loose.count():
                await self.page.keyboard.press("Escape")
                raise UpstreamPageChangedError(f"Doubao option '{name}' is not in the menu")
            target = loose.first
        else:
            await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError(f"Doubao option '{name}' is not in the menu")
        try:
            await target.click(timeout=8000)
        except Exception:
            await target.evaluate("el => el.click()")
        await asyncio.sleep(0.5)

    async def _pick_menu_item(self, control: str, name: str, prefix: bool = False) -> None:
        await self._open_control(control)
        await self._click_menu_item(name, prefix=prefix)

    async def _expect_trigger_text(self, control: str, name: str) -> None:
        label = (await self.page.locator(control).first.inner_text()).strip()
        if name not in label:
            raise UpstreamPageChangedError(
                f"Doubao did not apply '{name}' (control shows {label!r})"
            )

    async def _set_video_duration(self, duration: int) -> None:
        if not S.VIDEO_DURATION_MIN <= duration <= S.VIDEO_DURATION_MAX:
            raise UpstreamPageChangedError(
                f"duration {duration}s is outside "
                f"{S.VIDEO_DURATION_MIN}-{S.VIDEO_DURATION_MAX}s"
            )
        # Picking a ratio leaves the merged panel open (the options apply
        # live); clicking the trigger again would just close it, so only open
        # it when the slider is not already on screen.
        if (await self._video_thumb_state()) is None:
            await self._open_control(S.VIDEO_RATIO_CONTROL)
        # The panel re-renders and the thumb node is transient, so read its
        # state via JS (locators can wait on a node that was just replaced).
        state = await self._video_thumb_state()
        if state is None:
            await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError("the Doubao duration slider is not available")
        current = int(state["val"] or "0")
        target = duration - S.VIDEO_DURATION_MIN
        # The thumb is an 18px decorative span that Playwright never considers
        # clickable; focus it with JS and step with arrow keys (radix slider
        # moves on keydown — verified in poc/doubao_probe18.py).
        await self.page.evaluate(
            "() => { const t = document.querySelector("
            "'[role=\"slider\"][data-slot=\"slider-thumb\"]');"
            " if (t) t.focus(); }"
        )
        for _ in range(current):
            await self.page.keyboard.press("ArrowLeft")
        for _ in range(target):
            await self.page.keyboard.press("ArrowRight")
        await asyncio.sleep(0.6)
        state = await self._video_thumb_state()
        applied = int(state["val"]) if state and state["val"] is not None else -1
        if applied != target:
            await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError(
                f"the duration slider stayed at {applied}, expected {target}"
            )
        await self.page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

    async def _video_thumb_state(self) -> dict | None:
        for _ in range(10):
            state = await self.page.evaluate(
                "() => { const t = document.querySelector("
                "'[role=\"slider\"][data-slot=\"slider-thumb\"]');"
                " if (!t) return null;"
                " return {val: t.getAttribute('aria-valuenow'),"
                "         focused: document.activeElement === t,"
                "         panel: !!t.closest('[role=\"menu\"]')}; }"
            )
            if state and state.get("panel"):
                return state
            await asyncio.sleep(0.5)
        return None

    async def _pick_chip(self, index: int, name: str) -> None:
        chips = self.page.locator(S.MUSIC_CHIPS)
        if await chips.count() <= index:
            raise UpstreamPageChangedError(
                f"the music composer has no directive chip #{index}"
            )
        await chips.nth(index).click()
        try:
            await self.page.locator(S.POPUP_MENU).first.wait_for(state="visible", timeout=8000)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamPageChangedError("the music directive menu did not open") from exc
        await self._click_menu_item(name)
        label = (await chips.nth(index).inner_text()).strip()
        if name not in label:
            raise UpstreamPageChangedError(
                f"the music directive stayed at {label!r} after choosing '{name}'"
            )

    async def _fill_music_theme(self, text: str) -> None:
        theme = self.page.locator(S.MUSIC_THEME_INPUT)
        if await theme.count():
            await theme.first.fill(text)
            await asyncio.sleep(0.4)
            return
        editor = self.page.locator(S.EDITOR).first
        await editor.click()
        await self.page.keyboard.insert_text(text)
        await asyncio.sleep(0.4)

    async def _click_send(self) -> None:
        send = self.page.locator(S.SEND_BUTTON).first
        try:
            if await send.count() and await send.is_visible():
                await send.click(timeout=8000)
                return
        except Exception:  # noqa: BLE001
            pass
        await self.page.keyboard.press("Enter")

    async def _attach_files(self, attachments: list) -> None:
        paths = [str(a.path) if hasattr(a, "path") else str(a) for a in attachments]
        inputs = self.page.locator(f'{S.COMPOSER} input[type="file"]')
        if not await inputs.count():
            raise UpstreamPageChangedError("the Doubao composer has no file input")
        await inputs.first.set_input_files(paths)
        await asyncio.sleep(2)
