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

from playwright.async_api import Page

from freeaiproxy.config import Settings
from freeaiproxy.engine import yuanbao_selectors as S
from freeaiproxy.engine.browser_manager import BrowserManager
from freeaiproxy.engine.errors import (
    NotLoggedInError,
    UpstreamPageChangedError,
    UpstreamRateLimitedError,
)

if TYPE_CHECKING:
    from freeaiproxy.services.models import ModelSpec

logger = logging.getLogger("freeaiproxy.yuanbao")


@dataclass
class YuanbaoPreState:
    human_count: int
    ai_count: int
    last_human_text: str


SNAPSHOT_JS = """
() => {
  const humans = Array.from(document.querySelectorAll('.agent-chat__list__item--human'));
  const ais = Array.from(document.querySelectorAll('.agent-chat__list__item--ai'));
  const lastHuman = humans.length ? humans[humans.length - 1] : null;
  return {
    humanCount: humans.length,
    aiCount: ais.length,
    lastHumanText: lastHuman ? (lastHuman.innerText || '') : '',
  };
}
"""

HISTORY_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const ais = Array.from(document.querySelectorAll('.agent-chat__list__item--ai'));
  const last = ais.length ? ais[ais.length - 1] : null;
  return {
    humanCount: document.querySelectorAll('.agent-chat__list__item--human').length,
    aiCount: ais.length,
    lastTextLen: last ? (last.innerText || '').trim().length : 0,
    lastImages: last ? Array.from(last.querySelectorAll('img')).filter(visible).length : 0,
    lastFiles: last ? last.querySelectorAll('.file-card_fileCard__gfdDP').length : 0,
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
  const ais = Array.from(document.querySelectorAll('.agent-chat__list__item--ai'));
  const last = ais.length ? ais[ais.length - 1] : null;
  let answer = '';
  if (last) {
    const mds = Array.from(last.querySelectorAll('.hyc-common-markdown')).filter(el => {
      if (el.closest('[class*="deepsearch-cot__think"], [class*="deep-search-agent__think"]')) {
        return false;
      }
      if (String(el.className).includes('hyc-common-markdown-style-cot')) return false;
      if (el.closest('[class*="process-list"]')) return false;
      return visible(el);
    });
    answer = mds.map(el => (el.innerText || '').trim()).filter(Boolean).join('\\n\\n');
  }
  const send = Array.from(document.querySelectorAll('[aria-label="Send"]')).some(visible);
  const stop = Array.from(document.querySelectorAll('[aria-label="Stop Answering"]')).some(visible);
  return {
    answer,
    send,
    stop,
    outputting: last ? last.getAttribute('data-conv-outputting') : null,
    aiText: last ? (last.innerText || '').trim() : '',
    humanCount: document.querySelectorAll('.agent-chat__list__item--human').length,
    aiCount: ais.length,
    fileCards: last ? last.querySelectorAll('.file-card_fileCard__gfdDP').length : 0,
  };
}
"""

IMAGES_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const ais = Array.from(document.querySelectorAll('.agent-chat__list__item--ai'));
  const last = ais.length ? ais[ais.length - 1] : null;
  const urls = [];
  if (last) {
    for (const img of Array.from(last.querySelectorAll('img'))) {
      const src = img.currentSrc || img.src || '';
      if (!src) continue;
      if (!src.includes('text2img') && !src.includes('hunyuan-prod')) continue;
      const r = img.getBoundingClientRect();
      if (!visible(img) || r.width < 100 || r.height < 80) continue;
      if (!urls.includes(src)) urls.push(src);
    }
  }
  const send = Array.from(document.querySelectorAll('[aria-label="Send"]')).some(visible);
  const stop = Array.from(document.querySelectorAll('[aria-label="Stop Answering"]')).some(visible);
  return {
    urls,
    send,
    stop,
    outputting: last ? last.getAttribute('data-conv-outputting') : null,
  };
}
"""

IMAGE_DOWNLOAD_JS = """
(img, dlSelector) => {
  document.querySelectorAll('[data-fap-imgctl]').forEach(el => el.removeAttribute('data-fap-imgctl'));
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity || '1') > 0.05;
  };
  const ir = img.getBoundingClientRect();
  const matches = [];
  const selector = `${dlSelector}, [aria-label*="download" i], [class*="download" i]`;
  for (const el of document.querySelectorAll(selector)) {
    if (el === img || !visible(el)) continue;
    const r = el.getBoundingClientRect();
    const cx = r.x + r.width / 2;
    const cy = r.y + r.height / 2;
    if (cx < ir.left - 40 || cx > ir.right + 40 || cy < ir.top - 40 || cy > ir.bottom + 40) continue;
    matches.push(el);
  }
  matches.forEach((el, i) => el.setAttribute('data-fap-imgctl', String(i)));
  return {
    image: [Math.round(ir.left), Math.round(ir.top), Math.round(ir.width), Math.round(ir.height)],
    matched: matches.length,
    html: matches.slice(0, 2).map(el => (el.outerHTML || '').slice(0, 220)),
  };
}
"""

LOGIN_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const surfaces = Array.from(document.querySelectorAll(
    '[class*="hyc-login"], [class*="hyc-phone-login"], .agent-dialogue__tool__login'));
  if (surfaces.some(visible)) return true;
  const text = document.body.innerText || '';
  return text.includes('Not logged in') || text.includes('Please scan the QR code');
}
"""

MODEL_STATE_JS = """
() => {
  const button = document.querySelector("button[aria-label='Switch model']");
  const row = document.querySelector("button[aria-label='Select model']");
  return {
    mode: button ? (button.innerText || '').trim() : null,
    modelRow: row ? (row.innerText || '').replace(/\\s+/g, ' ').trim() : null,
  };
}
"""

PLACEHOLDER_JS = """
(sub) => {
  const editor = document.querySelector('.ql-editor');
  const placeholder = editor ? (editor.getAttribute('data-placeholder') || '') : '';
  if (placeholder.includes(sub)) return true;
  return (document.body.innerText || '').includes(sub);
}
"""


class YuanbaoDriver:
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
        await self._wait_history_ready()

    async def prepare(self, spec: ModelSpec) -> None:
        if await self._login_visible():
            raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
        await self._dismiss_tips()
        if spec.agent == "writing":
            await self._ensure_chat_model("Hy3", "Instant")
            await self._enter_writing()
        elif spec.agent == "image":
            await self._ensure_chat_model("Hy3", "Instant")
            await self._enter_image()
        elif spec.agent == "ppt":
            await self._ensure_chat_model("Hy4 preview", "Expert")
            await self._enter_ppt()
        else:
            if not spec.ui_model or not spec.ui_mode:
                raise UpstreamPageChangedError(f"model '{spec.id}' has no Yuanbao mapping")
            await self._ensure_chat_model(spec.ui_model, spec.ui_mode)
            if spec.search:
                await self._enable_search()
        if await self._login_visible():
            raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")

    async def snapshot(self) -> YuanbaoPreState:
        data = await self.page.evaluate(SNAPSHOT_JS)
        return YuanbaoPreState(
            human_count=data["humanCount"],
            ai_count=data["aiCount"],
            last_human_text=data["lastHumanText"],
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
        before = (await self.page.evaluate(SNAPSHOT_JS))["humanCount"]
        if attachments:
            await self._upload_attachments(attachments)

        editor = self.page.locator(S.INPUT_BOX).first
        await editor.click()
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Backspace")
        if text:
            await self.page.keyboard.insert_text(text)
        await asyncio.sleep(0.3)

        send = self.page.locator(S.SEND_BUTTON).first
        try:
            if await send.is_visible() and await send.is_enabled():
                await send.click(timeout=8000)
            else:
                await self.page.keyboard.press("Enter")
        except Exception:
            await self._js_click(S.SEND_BUTTON)
            await asyncio.sleep(0.3)

        fingerprint = text.strip().splitlines()[0][:12] if text.strip() else ""
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
            data = await self.page.evaluate(SNAPSHOT_JS)
            if data["humanCount"] > before:
                return
            if fingerprint and fingerprint in (data["lastHumanText"] or ""):
                return
        raise UpstreamPageChangedError("the message was not accepted by the page")

    async def current_chat_id(self) -> str:
        match = S.CHAT_PATH_RE.search(self.page.url)
        if not match:
            raise UpstreamPageChangedError(f"no chat id in page URL: {self.page.url}")
        return match.group(1)

    async def reply_stream(self, pre: YuanbaoPreState) -> AsyncIterator[str]:
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 0.3)
        t_first = self.settings.worker.timeout_first_token_s
        t_done = self.settings.worker.timeout_completion_s
        last_answer = ""
        last_activity = ""
        last_activity_at = time.monotonic()
        last_change = last_activity_at
        stable = 0
        saw_answer = False

        while True:
            await asyncio.sleep(poll)
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
            data = await self.page.evaluate(ANSWER_JS)
            now = time.monotonic()
            activity = data["aiText"] or ""
            if activity != last_activity:
                last_activity = activity
                last_activity_at = now
            answer = data["answer"] or ""
            if answer and answer != last_answer:
                saw_answer = True
                stable = 0
                last_change = now
                last_answer = answer
                yield answer
            elif answer:
                stable += 1
            else:
                stable = 0

            send = bool(data["send"])
            stop = bool(data["stop"])
            if saw_answer and send and not stop and stable >= 2:
                break
            if saw_answer and stable >= 8:
                break
            if not saw_answer and now - last_activity_at > t_first:
                break
            if saw_answer and now - last_change > t_done:
                break

    async def wait_completion(self, pre: YuanbaoPreState) -> str:
        text = ""
        async for snap in self.reply_stream(pre):
            text = snap
        return text

    async def drain(self, pre: YuanbaoPreState) -> str:
        return await self.wait_completion(pre)

    async def wait_images(self, pre: YuanbaoPreState) -> list[str]:
        poll = max(self.settings.worker.poll_interval_ms / 1000.0, 0.5)
        deadline = time.monotonic() + 300
        last_sig = ""
        stable = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(poll)
            if await self._login_visible():
                raise NotLoggedInError(f"{self.bm.login.name} shows the login panel")
            data = await self.page.evaluate(IMAGES_JS)
            urls = [self._full_image_url(url) for url in data["urls"]]
            sig = "|".join(urls)
            if sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            send = bool(data["send"])
            stop = bool(data["stop"])
            if urls and send and not stop and stable >= 2:
                return urls
        raise UpstreamPageChangedError("the page did not produce generated images")

    async def download_images(self, targets: list[Path]) -> list[Path | None]:
        last_ai = self.page.locator(S.AI_ITEM).last
        imgs = last_ai.locator("img")
        picked = []
        for i in range(await imgs.count()):
            img = imgs.nth(i)
            src = await img.get_attribute("src") or ""
            if "text2img" not in src and "hunyuan-prod" not in src:
                continue
            if not await img.is_visible():
                continue
            box = await img.bounding_box()
            if not box or box["width"] < 100 or box["height"] < 80:
                continue
            picked.append(img)

        results: list[Path | None] = []
        for i, target in enumerate(targets):
            if i >= len(picked):
                results.append(None)
                continue
            results.append(await self._download_image_ui(picked[i], target))
        return results

    async def _download_image_ui(self, img, target: Path) -> Path | None:
        try:
            await img.scroll_into_view_if_needed(timeout=5000)
            await img.hover(timeout=5000)
            await asyncio.sleep(0.6)
            probe = await img.evaluate(IMAGE_DOWNLOAD_JS, S.IMAGE_DOWNLOAD_BUTTON)
            if probe["matched"] <= 0:
                logger.warning(
                    "image download control not found for image at %s", probe["image"]
                )
                return None
            logger.debug("image download control matched: %s", probe["html"])
            control = self.page.locator("[data-fap-imgctl='0']").first
            async with self.page.expect_download(timeout=30000) as info:
                await control.click(timeout=5000)
            download = await info.value
            suffix = Path(download.suggested_filename or "").suffix.lower()
            path = target
            if suffix in (".png", ".jpg", ".jpeg", ".webp") and suffix != target.suffix.lower():
                path = target.with_suffix(suffix)
            await download.save_as(str(path))
            logger.info("saved generated image via page UI: %s", path)
            return path
        except Exception as exc:
            logger.warning("image UI download failed: %s", exc)
            return None
        finally:
            with contextlib.suppress(Exception):
                await self.page.evaluate(
                    "() => document.querySelectorAll('[data-fap-imgctl]')"
                    ".forEach(el => el.removeAttribute('data-fap-imgctl'))"
                )

    async def download_ppt(self, target: Path) -> str:
        last_ai = self.page.locator(S.AI_ITEM).last
        card = last_ai.locator(S.FILE_CARD).filter(has_text=".pptx").last
        await card.wait_for(state="visible", timeout=120000)

        download_button = self.page.locator(S.PPT_DOWNLOAD_BUTTON).first
        if not await download_button.is_visible():
            await card.click()
            await download_button.wait_for(state="visible", timeout=30000)
        await download_button.hover()
        pptx = self.page.locator("li.t-dropdown__item", has_text="PPTX").last
        await pptx.wait_for(state="visible", timeout=10000)
        try:
            async with self.page.expect_download(timeout=60000) as info:
                await pptx.click()
            download = await info.value
            await download.save_as(str(target))
            return download.suggested_filename or target.name
        except Exception as exc:
            raise UpstreamPageChangedError(f"failed to download the generated PPT: {exc}") from exc

    @staticmethod
    def _full_image_url(url: str) -> str:
        if "&imageMogr2" in url:
            return url.split("&imageMogr2", 1)[0]
        if "?imageMogr2" in url:
            return url.split("?imageMogr2", 1)[0]
        return url

    async def _wait_home(self) -> None:
        await self.page.locator(S.INPUT_BOX).first.wait_for(state="visible", timeout=30000)
        await self.page.locator(S.ADD_BUTTON).first.wait_for(state="visible", timeout=15000)
        await asyncio.sleep(0.5)
        await self._dismiss_tips()

    async def _wait_history_ready(self, timeout_s: float = 20.0) -> None:
        deadline = time.monotonic() + timeout_s
        last_sig: tuple | None = None
        stable = 0
        while time.monotonic() < deadline:
            data = await self.page.evaluate(HISTORY_JS)
            if data["aiCount"] == 0:
                await asyncio.sleep(0.5)
                continue
            has_content = (
                data["lastTextLen"] > 0 or data["lastImages"] > 0 or data["lastFiles"] > 0
            )
            sig = (data["humanCount"], data["aiCount"], data["lastTextLen"],
                   data["lastImages"], data["lastFiles"])
            if sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            if has_content and stable >= 2:
                return
            await asyncio.sleep(0.5)

    async def _dismiss_tips(self) -> None:
        with contextlib.suppress(Exception):
            await self._click_visible_text("Got It")

    async def _login_visible(self) -> bool:
        try:
            return bool(await self.page.evaluate(LOGIN_JS))
        except Exception:
            return False

    async def _rate_limit_check(self) -> None:
        for hint in S.RATE_LIMIT_HINTS:
            if await self.bm.hint_present(hint):
                raise UpstreamRateLimitedError("Yuanbao shows a rate-limit warning")

    async def _open_switch_menu(self) -> None:
        await self.page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        await self._js_click(S.SWITCH_MODEL_BUTTON)
        await asyncio.sleep(0.6)

    async def _read_model_state(self) -> dict:
        await self._open_switch_menu()
        state = await self.page.evaluate(MODEL_STATE_JS)
        await self.page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        if not state.get("modelRow"):
            raise UpstreamPageChangedError("could not open the Yuanbao model menu")
        return state

    async def _ensure_chat_model(self, ui_model: str, ui_mode: str) -> None:
        for _ in range(2):
            state = await self._read_model_state()
            model_ok = ui_model in (state.get("modelRow") or "")
            mode_ok = ui_mode in (state.get("mode") or "")
            if model_ok and mode_ok:
                return
            if not model_ok:
                await self._select_ui_model(ui_model)
            state = await self._read_model_state()
            if ui_mode not in (state.get("mode") or ""):
                await self._select_ui_mode(ui_mode)
        state = await self._read_model_state()
        if ui_model not in (state.get("modelRow") or "") or ui_mode not in (
            state.get("mode") or ""
        ):
            raise UpstreamPageChangedError(
                f"could not align Yuanbao to {ui_model} / {ui_mode}"
            )

    async def _select_ui_model(self, ui_model: str) -> None:
        await self._open_switch_menu()
        row = self.page.locator(S.SELECT_MODEL_ITEM).first
        try:
            await row.click(timeout=8000)
        except Exception:
            await self._js_click(S.SELECT_MODEL_ITEM)
        await asyncio.sleep(0.8)
        pattern = re.compile(rf"^{re.escape(ui_model)}")
        item = self.page.get_by_role("menuitemradio", name=pattern)
        if await item.count() == 0:
            await self._js_click(S.SELECT_MODEL_ITEM)
            await asyncio.sleep(0.8)
            item = self.page.get_by_role("menuitemradio", name=pattern)
        if await item.count() == 0:
            await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError(f"Yuanbao model '{ui_model}' is not available")
        try:
            await item.first.click(timeout=8000)
        except Exception:
            await item.first.evaluate("el => el.click()")
        await asyncio.sleep(1.0)

    async def _select_ui_mode(self, ui_mode: str) -> None:
        await self._open_switch_menu()
        item = self.page.get_by_role(
            "menuitemradio", name=re.compile(rf"^{re.escape(ui_mode)}")
        )
        if await item.count() == 0:
            await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError(f"Yuanbao mode '{ui_mode}' is not available")
        try:
            await item.first.click(timeout=8000)
        except Exception:
            await item.first.evaluate("el => el.click()")
        await asyncio.sleep(0.8)

    async def _enable_search(self) -> None:
        if await self._placeholder_contains(S.PLACEHOLDER_SEARCH):
            return
        await self._js_click(S.ADD_BUTTON)
        await asyncio.sleep(0.6)
        item = self.page.get_by_role("menuitem", name="Search")
        if await item.count() == 0:
            await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError("Yuanbao Search mode is not available")
        await item.first.click()
        await asyncio.sleep(0.6)
        await self._wait_placeholder(S.PLACEHOLDER_SEARCH, 10)

    async def _enter_writing(self) -> None:
        if await self._placeholder_contains(S.PLACEHOLDER_WRITING):
            return
        await self._click_aria("Writing")
        await self._wait_placeholder(S.PLACEHOLDER_WRITING, 15)

    async def _enter_image(self) -> None:
        if await self._placeholder_contains(S.PLACEHOLDER_IMAGE):
            return
        await self._click_aria("AI Image")
        await self._wait_placeholder(S.PLACEHOLDER_IMAGE, 15)

    async def select_image_ratio(self, ratio: str | None) -> None:
        """Set the AI Image aspect ratio; None/auto selects the web default."""
        target = S.IMAGE_RATIO_SMART if not ratio or ratio in ("auto", "smart") else ratio
        button = self.page.locator(S.IMAGE_RATIO_BUTTON).first
        if not await button.is_visible():
            raise UpstreamPageChangedError("Yuanbao image ratio control is not available")
        if target in (await button.inner_text()).strip():
            return
        await button.click()
        await asyncio.sleep(0.5)
        item = self.page.get_by_role("menuitemcheckbox", name=target, exact=True).first
        if await item.count() == 0:
            await self.page.keyboard.press("Escape")
            raise UpstreamPageChangedError(f"Yuanbao image ratio '{target}' is not available")
        try:
            await item.click(timeout=8000)
        except Exception:
            await item.evaluate("el => el.click()")
        await asyncio.sleep(0.5)
        if target not in (await button.inner_text()).strip():
            raise UpstreamPageChangedError(f"Yuanbao did not switch the image ratio to '{target}'")

    async def select_image_style(self, style: str) -> None:
        # Yuanbao's AI Image exposes no style control.
        return None

    async def select_video_options(
        self, ratio: str | None = None, duration: int | None = None
    ) -> None:
        # Yuanbao has no video skill.
        return None

    async def _enter_ppt(self) -> None:
        if await self._placeholder_contains(S.PLACEHOLDER_PPT):
            return
        if not await self._click_visible_text("Presentation Maker"):
            raise UpstreamPageChangedError("Yuanbao Presentation Maker is not available")
        await self._wait_placeholder(S.PLACEHOLDER_PPT, 20)

    async def _click_aria(self, name: str) -> None:
        loc = self.page.locator(f"[aria-label='{name}']")
        for i in range(await loc.count()):
            item = loc.nth(i)
            if await item.is_visible():
                try:
                    await item.click(timeout=8000)
                except Exception:
                    await item.evaluate("el => el.click()")
                await asyncio.sleep(0.6)
                return
        raise UpstreamPageChangedError(f"Yuanbao control '{name}' is not available")

    async def _click_visible_text(self, text: str) -> bool:
        return bool(await self.page.evaluate(
            """(want) => {
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0
                        && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const els = Array.from(document.querySelectorAll('button,[role="button"],div,span'))
                    .filter(visible)
                    .filter(el => (el.innerText || '').replace(/\\s+/g, ' ').trim() === want);
                if (!els.length) return false;
                els[els.length - 1].click();
                return true;
            }""",
            text,
        ))

    async def _js_click(self, selector: str) -> bool:
        return bool(await self.page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                el.click();
                return true;
            }""",
            selector,
        ))

    async def _placeholder_contains(self, text: str) -> bool:
        try:
            return bool(await self.page.evaluate(PLACEHOLDER_JS, text))
        except Exception:
            return False

    async def _wait_placeholder(self, text: str, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if await self._placeholder_contains(text):
                return
            await asyncio.sleep(0.3)
        raise UpstreamPageChangedError(f"Yuanbao did not enter the expected mode ({text})")

    async def _upload_attachments(self, attachments: list) -> None:
        images = [
            a for a in attachments
            if str(getattr(a, "media_type", "")).startswith("image/")
            or Path(a.path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
        ]
        documents = [a for a in attachments if a not in images]
        if images:
            await self._upload_group("Upload Image", images, wait_for_images=True)
        if documents:
            await self._upload_group("Local Files", documents, wait_for_images=False)

    async def _upload_group(
        self, menu_item: str, attachments: list, wait_for_images: bool
    ) -> None:
        last_error: Exception | None = None
        for _ in range(2):
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
            await self._js_click(S.ADD_BUTTON)
            await asyncio.sleep(0.8)
            loc = self.page.get_by_role("menuitem", name=menu_item)
            target = None
            for i in range(await loc.count()):
                item = loc.nth(i)
                if await item.is_visible():
                    target = item
                    break
            if target is None:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.4)
                continue
            box = await target.bounding_box()
            if box is None:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.4)
                continue
            try:
                async with self.page.expect_file_chooser(timeout=10000) as chooser_info:
                    await self.page.mouse.click(
                        box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    )
                chooser = await chooser_info.value
                await chooser.set_files([str(a.path) for a in attachments])
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
        if last_error is not None:
            raise UpstreamPageChangedError(
                f"failed to upload attachments: {last_error}"
            ) from last_error
        await asyncio.sleep(0.5)

        names = [a.filename for a in attachments]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if wait_for_images:
                ready = await self.page.evaluate(
                    """(names) => {
                        const imgs = Array.from(document.querySelectorAll('img'));
                        return names.every(n => imgs.some(img =>
                            img.alt === n && img.getBoundingClientRect().width > 0));
                    }""",
                    names,
                )
            else:
                ready = await self.page.evaluate(
                    """(names) => {
                        const container = document.querySelector(
                            '.agent-dialogue__content--common__input');
                        const withFiles = !!(container
                            && String(container.className).includes('with-files'));
                        const text = document.body.innerText || '';
                        const attrs = Array.from(document.querySelectorAll('[title],[alt]'))
                            .map(el => (el.getAttribute('title') || '')
                                + ' ' + (el.getAttribute('alt') || ''))
                            .join(' ');
                        const named = names.some(n => text.includes(n) || attrs.includes(n));
                        return withFiles || named;
                    }""",
                    names,
                )
            if ready:
                await asyncio.sleep(1.5 if not wait_for_images else 0.4)
                await self.page.keyboard.press("Escape")
                return
            await asyncio.sleep(0.4)
        await self.page.keyboard.press("Escape")
        raise UpstreamPageChangedError("Yuanbao did not accept the uploaded attachment")
