"""ChatGLM (智谱清言) browser driver.

Backs `chatglm-glm5.3` (deep-thinking chat via GLM-5.3; the thinking trace is
excluded from the reply) and `chatglm-image` (AI画图 in GLM Image mode with
the 去水印 switch forced on so the CDN url itself is watermark-free).
Calibrated 2026-10-23 against poc/chatglm_probe*.py dumps.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from playwright.async_api import Page

from freeaiproxy.config import Settings
from freeaiproxy.engine import chatglm_selectors as S
from freeaiproxy.engine.browser_manager import BrowserManager
from freeaiproxy.engine.errors import (
    NotLoggedInError,
    UpstreamPageChangedError,
    UpstreamRateLimitedError,
)

if TYPE_CHECKING:
    from freeaiproxy.services.models import ModelSpec

logger = logging.getLogger("freeaiproxy.chatglm")


@dataclass
class ChatGLMPreState:
    human_count: int
    ai_count: int
    last_human_text: str


SNAPSHOT_JS = """
() => {
  const qs = document.querySelectorAll('.conversation.question');
  const as = document.querySelectorAll('.answer');
  const lastQ = qs.length ? qs[qs.length - 1] : null;
  return {
    qCount: qs.length,
    aCount: as.length,
    lastQText: lastQ ? (lastQ.innerText || '') : '',
  };
}
"""

# Answer text = every .answer-content-wrap that is NOT the thinking trace.
ANSWER_JS = """
() => {
  const as = document.querySelectorAll('.answer');
  const last = as.length ? as[as.length - 1] : null;
  let text = '';
  if (last) {
    const wraps = Array.from(last.querySelectorAll('.answer-content-wrap'))
      .filter((e) => !e.classList.contains('text-advance-thinking-content'));
    text = wraps.map((e) => (e.innerText || '').trim()).filter(Boolean).join('\\n\\n');
  }
  const enter = document.querySelector('.enter');
  const loading = enter ? enter.classList.contains('searching') : false;
  return { aCount: as.length, text, loading };
}
"""

IMAGES_JS = """
() => {
  const srcs = [];
  const as = document.querySelectorAll('.answer');
  const last = as.length ? as[as.length - 1] : null;
  if (last) {
    for (const img of last.querySelectorAll('.img-answer-item img.limitSize')) {
      const src = img.currentSrc || img.src || '';
      if (!src.includes('sfile.chatglm.cn')) continue;
      const r = img.getBoundingClientRect();
      if (r.width < 80 || r.height < 80) continue;
      if (!srcs.includes(src)) srcs.push(src);
    }
  }
  const enter = document.querySelector('.enter');
  const loading = enter ? enter.classList.contains('searching') : false;
  return { srcs, loading };
}
"""

CLICK_TEXT_JS = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return false;
  el.click();
  return true;
}
"""

CLICK_PROMPT_ITEM_JS = """
(needle) => {
  const els = Array.from(document.querySelectorAll('.prompt-item'));
  const el = needle === null ? els[0] : els.find((e) => (e.innerText || '').includes(needle));
  if (!el) return false;
  el.click();
  return true;
}
"""

# Click the innermost element whose trimmed text starts with `text` inside the
# visible popover container that contains `mustContain`.
CLICK_INNER_TEXT_JS = """
(args) => {
  const scope = Array.from(document.querySelectorAll(args.scope))
    .find((e) => e.getBoundingClientRect().width > 0
                 && (!args.mustContain || (e.innerText || '').includes(args.mustContain)));
  if (!scope) return false;
  const cands = Array.from(scope.querySelectorAll('*'))
    .filter((e) => (e.innerText || '').trim().startsWith(args.text));
  if (!cands.length) return false;
  cands[cands.length - 1].click();
  return true;
}
"""

PROMPT_ITEM_TEXT_JS = """
(needle) => {
  const el = Array.from(document.querySelectorAll('.prompt-item'))
    .find((e) => (e.innerText || '').includes(needle));
  return el ? (el.innerText || '').replace(/\\s+/g, ' ').trim() : null;
}
"""

TRIGGER_TEXT_JS = """
() => {
  const el = document.querySelector('.think-mode-trigger');
  return el ? (el.innerText || '').replace(/\\s+/g, ' ').trim() : null;
}
"""

# Inspect the visible 模式选择 popover: returns [checkedLabel, targetFound].
MODE_RADIOS_JS = """
() => {
  const pop = Array.from(document.querySelectorAll('.draw_options_popover'))
    .find((e) => e.getBoundingClientRect().width > 0 && (e.innerText || '').includes('模式选择'));
  if (!pop) return null;
  const radios = Array.from(pop.querySelectorAll('.el-radio'));
  const norm = (t) => (t || '').replace(/\\s+/g, ' ').trim();
  const checked = radios.find((r) => r.classList.contains('is-checked'));
  const target = radios.find((r) => norm(r.innerText).startsWith('GLM Image'));
  return [checked ? norm(checked.innerText) : null, !!target];
}
"""

MODE_CHECKED_JS = """
() => {
  const pop = Array.from(document.querySelectorAll('.draw_options_popover'))
    .find((e) => e.getBoundingClientRect().width > 0 && (e.innerText || '').includes('模式选择'));
  if (!pop) return null;
  const checked = Array.from(pop.querySelectorAll('.el-radio')).find((r) => r.classList.contains('is-checked'));
  return checked ? (checked.innerText || '').replace(/\\s+/g, ' ').trim() : null;
}
"""

MODEL_ITEM_TEXT_JS = """
(name) => {
  const items = Array.from(document.querySelectorAll('.think-mode-item'));
  const hit = items.find((e) => {
    const n = e.querySelector('.item-name');
    return n && (n.innerText || '').trim() === name;
  });
  if (!hit) return false;
  hit.click();
  return true;
}
"""


class ChatGLMDriver:
    def __init__(self, bm: BrowserManager, settings: Settings):
        self.bm = bm
        self.settings = settings

    @property
    def page(self) -> Page:
        if self.bm.page is None:
            raise UpstreamPageChangedError("browser is not running")
        return self.bm.page

    async def goto_new_chat(self) -> None:
        await self.page.goto(S.CHAT_URL, wait_until="domcontentloaded")
        await self._wait_home()

    async def goto_chat(self, chat_ref: str) -> None:
        if chat_ref.startswith(S.DRAW_REF_PREFIX):
            cid = chat_ref[len(S.DRAW_REF_PREFIX) :]
            await self.page.goto(f"{S.DRAW_URL}&cid={cid}", wait_until="domcontentloaded")
        else:
            await self.page.goto(f"{S.CHAT_URL}&cid={chat_ref}", wait_until="domcontentloaded")
        await self._wait_home()

    async def prepare(self, spec: ModelSpec) -> None:
        if await self._login_visible():
            raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
        want_draw = spec.agent == "image"
        on_draw = "/gdetail/" in self.page.url
        if want_draw != on_draw:
            await self.page.goto(S.DRAW_URL if want_draw else S.CHAT_URL, wait_until="domcontentloaded")
            await self._wait_home()
        elif not want_draw:
            await self._dismiss_toast()
        if want_draw:
            await self._ensure_watermark_free()
            await self._ensure_draw_mode(S.MODE_GLM_IMAGE)
        else:
            await self._ensure_model(S.MODEL_GLM53)

    async def snapshot(self) -> ChatGLMPreState:
        data = await self.page.evaluate(SNAPSHOT_JS)
        return ChatGLMPreState(
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
        if attachments:
            await self._upload_reference_images(attachments)

        # Native clicks are swallowed by this page; fill() focuses without
        # clicking, and Enter submits from the focused textarea.
        box = self.page.locator(S.INPUT_BOX).first
        await box.fill(text)
        await asyncio.sleep(0.3)
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

    async def _upload_reference_images(self, attachments: list) -> None:
        # The AI画图 composer exposes a hidden input[type=file] inside
        # .ai-draw-upload; each accepted file renders a thumbnail <img> there.
        for uploaded, att in enumerate(attachments, start=1):
            inp = self.page.locator(S.DRAW_UPLOAD_INPUT).first
            try:
                await inp.set_input_files(str(att.path), timeout=10000)
            except Exception as exc:  # noqa: BLE001
                raise UpstreamPageChangedError(
                    f"failed to upload reference image: {exc}"
                ) from exc
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                ready = await self.page.evaluate(
                    """(want) => document.querySelectorAll(
                        '.ai-draw-upload img[src]').length >= want"""
                    ,
                    uploaded,
                )
                if ready:
                    await asyncio.sleep(0.5)
                    break
                await asyncio.sleep(0.4)
            else:
                raise UpstreamPageChangedError("ChatGLM did not accept the reference image")

    async def current_chat_id(self) -> str:
        match = S.CID_RE.search(self.page.url)
        if not match:
            raise UpstreamPageChangedError(f"no cid in page URL: {self.page.url}")
        if "/gdetail/" in self.page.url:
            return S.DRAW_REF_PREFIX + match.group(1)
        return match.group(1)

    async def reply_stream(self, pre: ChatGLMPreState) -> AsyncIterator[str]:
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

    async def wait_completion(self, pre: ChatGLMPreState) -> str:
        text = ""
        async for snap in self.reply_stream(pre):
            text = snap
        return text

    async def drain(self, pre: ChatGLMPreState) -> str:
        return await self.wait_completion(pre)

    async def select_image_ratio(self, ratio: str | None) -> None:
        # The draw toolbar exposes a ratio chip (1:1), but its popover options
        # are not calibrated yet; requested sizes are accepted and ignored.
        return None

    async def select_image_style(self, style: str) -> None:
        # The ChatGLM draw page exposes no style control.
        return None

    async def select_video_options(
        self, ratio: str | None = None, duration: int | None = None
    ) -> None:
        # ChatGLM has no video skill.
        return None

    async def wait_images(self, pre: ChatGLMPreState) -> list[str]:
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 0.5)
        deadline = time.monotonic() + self.settings.worker.timeout_completion_s
        last_sig = ""
        stable = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(poll)
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
            data = await self.page.evaluate(IMAGES_JS)
            srcs = [self._original_url(u) for u in data["srcs"]]
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
        srcs = [self._original_url(u) for u in data["srcs"]]
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
                    logger.warning("chatglm image download failed: %s", exc)
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
    def _original_url(url: str) -> str:
        # Thumbnails carry ?image_process=format,webp; the bare url is the
        # original jpg (watermark-free when 去水印 was on at generation).
        return url.split("?", 1)[0]

    async def _ensure_model(self, name: str) -> None:
        current = await self.page.evaluate(TRIGGER_TEXT_JS)
        if current and name in current:
            return
        if not await self.page.evaluate(CLICK_TEXT_JS, S.MODEL_TRIGGER):
            raise UpstreamPageChangedError("ChatGLM model trigger is missing")
        await asyncio.sleep(1.2)
        picked = await self.page.evaluate(MODEL_ITEM_TEXT_JS, name)
        await asyncio.sleep(1.2)
        current = await self.page.evaluate(TRIGGER_TEXT_JS)
        if not picked or not current or name not in current:
            raise UpstreamPageChangedError(f"ChatGLM model '{name}' could not be selected")

    async def _ensure_watermark_free(self) -> None:
        label = await self.page.evaluate(PROMPT_ITEM_TEXT_JS, S.WM_TOGGLE_TEXT)
        if label is None:
            raise UpstreamPageChangedError("ChatGLM 去水印 toggle is missing")
        if S.WM_ON_TEXT in label:
            return
        if not await self.page.evaluate(CLICK_PROMPT_ITEM_JS, S.WM_TOGGLE_TEXT):
            raise UpstreamPageChangedError("ChatGLM 去水印 toggle is missing")
        await asyncio.sleep(1.2)
        ok = await self.page.evaluate(
            CLICK_INNER_TEXT_JS,
            {"scope": S.DRAW_POPOVER, "mustContain": "图片去水印", "text": S.WM_ON_OPTION},
        )
        await asyncio.sleep(1.0)
        label = await self.page.evaluate(PROMPT_ITEM_TEXT_JS, S.WM_TOGGLE_TEXT)
        if not ok or not label or S.WM_ON_TEXT not in label:
            raise UpstreamPageChangedError("ChatGLM 去水印 could not be turned on")

    async def _ensure_draw_mode(self, mode: str) -> None:
        # The mode chip's innerText is CSS-truncated (e.g. "GLM" for
        # "GLM Image"), so verify via the popover radio's checked state
        # instead of the chip label.
        if not await self.page.evaluate(CLICK_PROMPT_ITEM_JS, None):
            raise UpstreamPageChangedError("ChatGLM draw mode chip is missing")
        await asyncio.sleep(1.2)
        state = await self.page.evaluate(MODE_RADIOS_JS)
        if state is None:
            raise UpstreamPageChangedError("ChatGLM draw mode popover did not open")
        checked, found = state
        if checked and checked.startswith(mode):
            await self.page.keyboard.press("Escape")
            return
        if not found:
            await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError(f"ChatGLM draw mode '{mode}' is not offered")
        await self.page.evaluate(
            CLICK_INNER_TEXT_JS,
            {"scope": S.DRAW_POPOVER, "mustContain": "模式选择", "text": mode},
        )
        await asyncio.sleep(0.8)
        checked2 = await self.page.evaluate(MODE_CHECKED_JS)
        await self.page.keyboard.press("Escape")
        if not checked2 or not checked2.startswith(mode):
            raise UpstreamPageChangedError(f"ChatGLM draw mode '{mode}' could not be selected")

    async def _dismiss_toast(self) -> None:
        return None

    async def _wait_new_answer(self, pre: ChatGLMPreState, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            if await self._login_visible():
                raise NotLoggedInError("login panel appeared while waiting for the reply")
            data = await self.page.evaluate(ANSWER_JS)
            if data["aCount"] > pre.ai_count and data["text"]:
                return True
        return False

    async def _login_visible(self) -> bool:
        return await self.bm.any_login_hint()

    async def _rate_limit_check(self) -> None:
        for hint in S.RATE_LIMIT_HINTS:
            if await self.bm.hint_present(hint):
                raise UpstreamRateLimitedError("ChatGLM shows a rate-limit warning")

    async def _wait_home(self) -> None:
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        try:
            await self.page.locator(S.INPUT_BOX).first.wait_for(state="visible", timeout=30000)
        except Exception as exc:
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} shows the login panel") from exc
            raise UpstreamPageChangedError("ChatGLM composer not found after navigation") from exc
        await asyncio.sleep(0.6)
