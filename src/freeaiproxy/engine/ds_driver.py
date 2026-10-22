from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from playwright.async_api import Locator, Page

from freeaiproxy.config import Settings
from freeaiproxy.engine import selectors as S
from freeaiproxy.engine.browser_manager import BrowserManager
from freeaiproxy.engine.errors import (
    NotLoggedInError,
    UpstreamPageChangedError,
    UpstreamRateLimitedError,
)

if TYPE_CHECKING:
    from freeaiproxy.services.models import ModelSpec

# The DeepSeek message list is virtualised: the assistant node count does not
# grow while a reply streams, only the text does. The reply is therefore
# located by anchoring on the last user message (the one we just sent) and
# taking every assistant node that follows it in document order. Anchoring on
# DOM order instead of comparing text avoids a subtle bug: when the new reply
# happens to be identical to the previous one, text comparison never detects
# the change and the request would time out with an empty answer.
_SNAPSHOT_JS = """
() => {
  const userNodes = Array.from(document.querySelectorAll('.ds-message'))
    .filter(el => el.querySelector('.ds-collapsible-text'));
  const lastUser = userNodes.length ? userNodes[userNodes.length - 1] : null;
  const all = Array.from(document.querySelectorAll('.ds-assistant-message-main-content'));
  let replies = all;
  if (lastUser) {
    replies = all.filter(el =>
      (lastUser.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0);
  }
  const reply = replies.length ? replies[replies.length - 1] : null;
  const thinkNodes = Array.from(document.querySelectorAll('.ds-think-content'));
  const lastThink = thinkNodes.length ? thinkNodes[thinkNodes.length - 1] : null;
  const primary = document.querySelector('.ds-button.ds-button--primary.ds-button--circle');
  const primaryCls = primary ? String(primary.className) : '';
  return {
    replyCount: replies.length,
    replyText: reply ? (reply.innerText || '') : '',
    thinkLen: lastThink ? (lastThink.innerText || '').length : 0,
    userCount: userNodes.length,
    userText: lastUser ? (lastUser.innerText || '') : '',
    generating: primaryCls.indexOf('_52c986b') >= 0 && primaryCls.indexOf('bd74640a') < 0,
  };
}
"""


_THINK_TOGGLE_JS = """
() => {
  const toggles = Array.from(document.querySelectorAll('.ds-toggle-button'));
  for (const t of toggles) {
    const label = t.parentElement ? (t.parentElement.textContent || '') : '';
    if (label.includes('DeepThink')) {
      return {
        enabled: t.getAttribute('aria-pressed') === 'true',
        selected: String(t.className).includes('--selected'),
      };
    }
  }
  return {enabled: null, selected: false};
}
"""


class PreState:

    def __init__(
        self,
        user_count: int = 0,
        user_text: str = "",
        reply_count: int = 0,
        reply_text: str = "",
    ):
        self.user_count = user_count
        self.user_text = user_text
        self.reply_count = reply_count
        self.reply_text = reply_text


class DeepSeekDriver:
    def __init__(self, bm: BrowserManager, settings: Settings):
        self.bm = bm
        self.settings = settings

    @property
    def page(self) -> Page:
        if self.bm.page is None:
            raise UpstreamPageChangedError("browser is not running")
        return self.bm.page

    async def prepare(self, spec: ModelSpec) -> None:
        return None

    async def _first_visible(self, candidates: list[str]) -> tuple[str, Locator] | None:
        page = self.page
        for sel in candidates:
            try:
                loc = page.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    return sel, loc.first
            except Exception:
                continue
        return None

    async def _wait_ready(self, timeout_s: float = 25.0) -> None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            if await self.bm.any_login_hint():
                raise NotLoggedInError("DeepSeek page shows the login panel")
            if await self._first_visible(S.INPUT_BOX_CANDIDATES) is not None:
                return
            await asyncio.sleep(0.3)
        raise UpstreamPageChangedError("chat input box not found after navigation")

    async def goto_new_chat(self) -> None:
        await self.page.goto(S.NEW_CHAT_URL, wait_until="domcontentloaded")
        await self._wait_ready()

    async def goto_chat(self, chat_id: str) -> None:
        url = f"{S.CHAT_BASE_URL}a/chat/s/{chat_id}"
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._wait_ready(timeout_s=30.0)

    async def snapshot(self) -> PreState:
        data = await self.page.evaluate(_SNAPSHOT_JS)
        return PreState(
            user_count=data["userCount"],
            user_text=data["userText"],
            reply_count=data["replyCount"],
            reply_text=data["replyText"],
        )

    async def _read(self) -> dict:
        return await self.page.evaluate(_SNAPSHOT_JS)

    async def _rate_limit_check(self) -> None:
        for hint in ("频率过高", "操作频繁", "稍后再试"):
            if await self.bm.hint_present(hint):
                raise UpstreamRateLimitedError("DeepSeek shows a rate-limit warning")

    @staticmethod
    def _fingerprint(text: str) -> str:
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        return first_line[:12]

    async def read_think_state(self) -> bool | None:
        """Whether the DeepThink toggle is currently on (None when not found)."""
        state = await self.page.evaluate(_THINK_TOGGLE_JS)
        return state.get("enabled")

    async def set_think_mode(self, enabled: bool) -> bool:
        """Turn the DeepThink toggle on or off. Returns the resulting state."""
        current = await self.read_think_state()
        if current is None:
            raise UpstreamPageChangedError("DeepThink toggle not found on the page")
        if current == enabled:
            return current
        clicked = await self.page.evaluate(
            """(want) => {
                const toggles = Array.from(document.querySelectorAll('.ds-toggle-button'));
                for (const t of toggles) {
                    const label = t.parentElement ? (t.parentElement.textContent || '') : '';
                    if (!label.includes('DeepThink')) continue;
                    if (t.getAttribute('aria-pressed') === (want ? 'true' : 'false')) {
                        return 'already';
                    }
                    t.click();
                    return 'clicked';
                }
                return 'missing';
            }""",
            enabled,
        )
        if clicked == "missing":
            raise UpstreamPageChangedError("DeepThink toggle not found on the page")
        for _ in range(20):
            await asyncio.sleep(0.15)
            state = await self.read_think_state()
            if state == enabled:
                return state
        raise UpstreamPageChangedError("DeepThink toggle did not reach the requested state")

    async def ensure_think_mode(self, enabled: bool) -> bool:
        """Make sure the DeepThink toggle matches the requested mode.

        Reasoning mode is opt-in, so a missing toggle is only fatal when the
        caller asked for it; in normal mode the page default (off) is fine.
        """
        current = await self.read_think_state()
        if current is None:
            if enabled:
                raise UpstreamPageChangedError("DeepThink toggle not found on the page")
            return False
        if current == enabled:
            return current
        await self.set_think_mode(enabled)
        return enabled

    async def attach_files(self, attachments: list) -> None:
        """Upload files into the composer and wait until the page accepted them.

        The composer owns a hidden <input type="file" multiple>, so the files
        are handed over directly instead of driving the native file chooser.
        An image shows up as a blob preview, a document as a file card; in
        both cases the file name reaches the DOM, which is the signal that
        the upload finished.
        """
        paths = [str(a.path) for a in attachments]
        names = [a.filename for a in attachments]
        if not paths:
            return
        try:
            await self.page.set_input_files(S.FILE_INPUT, paths)
        except Exception as exc:
            raise UpstreamPageChangedError(f"failed to attach files: {exc}") from exc
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            missing = await self._missing_attachment_names(names)
            if not missing:
                return
            if await self._upload_error_present():
                raise UpstreamPageChangedError("DeepSeek rejected the uploaded file")
            await asyncio.sleep(0.4)
        raise UpstreamPageChangedError("the uploaded file did not appear in the composer")

    async def _missing_attachment_names(self, names: list[str]) -> list[str]:
        try:
            present = await self.page.evaluate(
                """(names) => {
                    const out = {};
                    for (const n of names) {
                        let found = false;
                        const img = document.querySelector('img[alt="' + n + '"]');
                        if (img) { found = true; }
                        if (!found) {
                            const nodes = document.querySelectorAll('div, span');
                            for (const el of nodes) {
                                if (el.children.length === 0
                                    && (el.textContent || '').trim() === n) {
                                    found = true;
                                    break;
                                }
                            }
                        }
                        out[n] = found;
                    }
                    return out;
                }""",
                names,
            )
        except Exception:
            return names
        return [n for n in names if not present.get(n)]

    async def _upload_error_present(self) -> bool:
        try:
            return bool(
                await self.page.evaluate(
                    """() => Array.from(document.querySelectorAll('[class*="error"], [role="alert"]'))
                        .some(el => (el.innerText || '').trim().length > 0)"""
                )
            )
        except Exception:
            return False

    async def send_user_message(
        self,
        text: str,
        attachments: list | None = None,
        think: bool = False,
        spec: ModelSpec | None = None,
    ) -> None:
        use_think = spec.think if spec is not None else think
        if await self.bm.any_login_hint():
            raise NotLoggedInError("DeepSeek page shows the login panel")
        await self._rate_limit_check()
        found = await self._first_visible(S.INPUT_BOX_CANDIDATES)
        if found is None:
            raise UpstreamPageChangedError("chat input box not found")
        _, box = found
        await box.click()
        with contextlib.suppress(Exception):
            await box.fill("")
        await self.ensure_think_mode(use_think)
        if attachments:
            await self.attach_files(attachments)
        if text:
            await self.page.keyboard.insert_text(text)
        await asyncio.sleep(0.25)
        await self.page.keyboard.press("Enter")

        fingerprint = self._fingerprint(text)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20:
            await asyncio.sleep(0.3)
            if await self.bm.any_login_hint():
                raise NotLoggedInError("login panel appeared after sending")
            data = await self._read()
            if fingerprint and fingerprint in (data["userText"] or ""):
                return
        raise UpstreamPageChangedError("the message was not accepted by the page")

    async def current_chat_id(self) -> str:
        m = S.CHAT_URL_ID_RE.search(self.page.url)
        if not m:
            raise UpstreamPageChangedError(f"no chat id in page URL: {self.page.url}")
        return m.group(1)

    async def reply_stream(self, pre: PreState) -> AsyncIterator[str]:
        """Yield the growing text of the assistant reply that follows our message.

        In reasoning mode the model first renders a thinking block
        (.ds-think-content) and only then the answer
        (.ds-assistant-message-main-content). Thinking progress counts as
        activity, otherwise a long reasoning run would trip the first-token
        timeout before the answer ever appears.
        """
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 0.2)
        t_first = self.settings.worker.timeout_first_token_s
        t_done = self.settings.worker.timeout_completion_s
        t0 = time.monotonic()
        last_text = ""
        last_think = -1
        stable = 0
        last_change = t0
        saw_any = False
        while True:
            await asyncio.sleep(poll)
            if await self.bm.any_login_hint():
                raise NotLoggedInError("login panel appeared while waiting for the reply")
            data = await self._read()
            text = data["replyText"] or ""
            think_len = data.get("thinkLen", 0) or 0
            now = time.monotonic()
            if text and text != last_text:
                saw_any = True
                stable = 0
                last_change = now
                last_text = text
                yield text
            else:
                stable += 1
            if think_len != last_think:
                last_think = think_len
                last_change = now
            if saw_any:
                if stable >= 2 and (not data["generating"] or stable >= 8):
                    break
                if now - last_change > t_done:
                    break
            else:
                if now - last_change > t_first:
                    break

    async def wait_completion(self, pre: PreState) -> str:
        text = ""
        async for snap in self.reply_stream(pre):
            text = snap
        return text

    async def drain(self, pre: PreState) -> str:
        return await self.wait_completion(pre)
