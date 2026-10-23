from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from freeaiproxy.config import Settings
from freeaiproxy.db import Database
from freeaiproxy.engine.browser_manager import BrowserManager
from freeaiproxy.engine.errors import NotLoggedInError, UpstreamError
from freeaiproxy.services import session_service as SS
from freeaiproxy.services.artifacts import SavedImage, file_url, new_artifact_path
from freeaiproxy.services.models import MODELS, ModelSpec
from freeaiproxy.services.queue import RequestQueue
from freeaiproxy.services.session_service import ROLE_ASSISTANT, ROLE_USER, Msg, Resolution

logger = logging.getLogger("freeaiproxy.chat")


def model_uses_reasoning(model: str) -> bool:
    spec = MODELS.get(model)
    return bool(spec and spec.think)


@dataclass
class ChatOutcome:
    conv_id: str
    content: str
    model: str


class ChatService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        bm: BrowserManager,
        driver: Any,
        queue: RequestQueue,
        provider: str,
    ):
        self.settings = settings
        self.db = db
        self.bm = bm
        self.driver = driver
        self.queue = queue
        self.provider = provider

    def has_capacity(self) -> bool:
        return self.queue.has_capacity()

    @property
    def active_requests(self) -> int:
        return self.queue.active

    @property
    def waiting_requests(self) -> int:
        return self.queue.waiting

    def queue_stats(self) -> dict:
        return self.queue.stats()

    async def prepare(
        self,
        messages: list[Msg],
        session_id: str | None,
        sys_text: str = "",
        spec: ModelSpec | None = None,
    ) -> Resolution:
        mode = spec.mode if spec is not None else "chat"
        return await SS.resolve(
            self.db,
            session_id=session_id,
            messages=messages,
            sys_text=sys_text,
            provider=self.provider,
            mode=mode,
        )

    async def run(self, res: Resolution, spec: ModelSpec) -> ChatOutcome:
        if res.adopt_pairs:
            for role, content in res.adopt_pairs:
                await self.db.append_history(res.conv_id, role, content)
            await self.db.touch(res.conv_id)
            return ChatOutcome(conv_id=res.conv_id, content=res.cached_reply or "", model=spec.id)
        if res.mode == "repeat":
            return ChatOutcome(conv_id=res.conv_id, content=res.cached_reply or "", model=spec.id)

        waited_ms = await self.queue.acquire()
        if waited_ms > 1000:
            logger.info("request %s waited %dms in the queue", res.conv_id, waited_ms)
        try:
            await self.bm.ensure_available()
            if spec.kind == "image":
                content = await self._execute_image(res, spec)
            elif spec.kind == "music":
                content = await self._execute_music(res, spec)
            elif spec.agent == "ppt":
                content = await self._execute_ppt(res, spec)
            else:
                content = await self._execute(res, spec)
            return ChatOutcome(conv_id=res.conv_id, content=content, model=spec.id)
        finally:
            self.queue.release()

    async def generate_images(
        self, prompt: str, spec: ModelSpec, limit: int = 4, ratio: str | None = None
    ) -> list[SavedImage]:
        if spec.kind != "image":
            raise UpstreamError(f"model '{spec.id}' does not generate images")
        waited_ms = await self.queue.acquire()
        if waited_ms > 1000:
            logger.info("image request waited %dms in the queue", waited_ms)
        try:
            await self.bm.ensure_available()
            await self.driver.goto_new_chat()
            await self.driver.prepare(spec)
            await self.driver.select_image_ratio(ratio)
            pre = await self.driver.snapshot()
            await self.driver.send_user_message(prompt, attachments=[], spec=spec)
            urls = await self.driver.wait_images(pre)
            return await self._save_generated_images(urls[:limit])
        finally:
            self.queue.release()

    async def generate_music(
        self,
        prompt: str,
        spec: ModelSpec,
        *,
        style: str | None = None,
        voice: str | None = None,
        lyrics: str | None = None,
    ) -> SavedImage:
        if spec.kind != "music":
            raise UpstreamError(f"model '{spec.id}' does not generate music")
        waited_ms = await self.queue.acquire()
        if waited_ms > 1000:
            logger.info("music request waited %dms in the queue", waited_ms)
        try:
            await self.bm.ensure_available()
            await self.driver.goto_new_chat()
            await self.driver.prepare(spec)
            await self.driver.select_music_options(
                style=style, voice=voice, custom_lyrics=bool(lyrics)
            )
            pre = await self.driver.snapshot()
            await self.driver.send_user_message(
                (lyrics or prompt).strip(), attachments=[], spec=spec
            )
            urls = await self.driver.wait_music(pre)
            return await self._save_music(urls[0])
        finally:
            self.queue.release()

    async def _navigate_and_send(self, res: Resolution, spec: ModelSpec) -> Any:
        conv = res.conv
        try:
            if conv["chat_id"]:
                await self.driver.goto_chat(conv["chat_id"])
            else:
                await self.driver.goto_new_chat()
            await self.driver.prepare(spec)
            pre = await self.driver.snapshot()
            await self.driver.send_user_message(
                res.pending_user or "",
                attachments=res.pending_attachments,
                spec=spec,
            )
            return pre
        except NotLoggedInError:
            self.bm.mark_logged_out()
            raise

    async def _finalize(self, res: Resolution, reply: str, spec: ModelSpec) -> None:
        if not res.conv["chat_id"]:
            chat_ref = await self.driver.current_chat_id()
            await self.db.bind_chat_id(res.conv_id, chat_ref)
            res.conv["chat_id"] = chat_ref
        await self.db.append_history(res.conv_id, ROLE_USER, res.pending_user or "")
        await self.db.append_history(res.conv_id, ROLE_ASSISTANT, reply)
        await self.db.set_mode(res.conv_id, spec.mode)
        await self.db.touch(res.conv_id)

    async def _execute(self, res: Resolution, spec: ModelSpec) -> str:
        assert res.pending_user is not None
        pre = await self._navigate_and_send(res, spec)
        reply = await self.driver.wait_completion(pre)
        if not reply:
            logger.warning("first-token timeout: returning empty reply for %s", res.conv_id)
        await self._finalize(res, reply, spec)
        return reply

    async def _execute_image(self, res: Resolution, spec: ModelSpec) -> str:
        pre = await self._navigate_and_send(res, spec)
        urls = await self.driver.wait_images(pre)
        saved = await self._save_generated_images(urls)
        content = self._image_markdown(saved)
        await self._finalize(res, content, spec)
        return content

    async def _execute_music(self, res: Resolution, spec: ModelSpec) -> str:
        pre = await self._navigate_and_send(res, spec)
        urls = await self.driver.wait_music(pre)
        clip = await self._save_music(urls[0])
        content = f"[generated music]({file_url(self.settings, clip.name)})"
        await self._finalize(res, content, spec)
        return content

    async def _execute_ppt(self, res: Resolution, spec: ModelSpec) -> str:
        pre = await self._navigate_and_send(res, spec)
        summary = await self.driver.wait_completion(pre)
        return await self._build_ppt_content(summary, spec, res)

    async def _save_generated_images(self, urls: list[str]) -> list[SavedImage]:
        if not urls:
            raise UpstreamError("the page did not produce any generated images")
        targets = [new_artifact_path(".png") for _ in urls]
        ui_paths: list[Path | None] = []
        try:
            ui_paths = await self.driver.download_images(targets)
        except Exception as exc:
            logger.warning("page UI image download failed; falling back to URLs: %s", exc)
        saved: list[SavedImage | None] = [None] * len(urls)
        for i, path in enumerate(ui_paths[: len(urls)]):
            if path is not None and path.is_file() and path.stat().st_size > 0:
                saved[i] = SavedImage(name=path.name, path=path)
        missing = [i for i, item in enumerate(saved) if item is None]
        if missing:
            logger.warning("direct-download fallback for %d generated image(s)", len(missing))
            fallback = await self._download_images([urls[i] for i in missing])
            for i, item in zip(missing, fallback, strict=True):
                saved[i] = item
        return [item for item in saved if item is not None]

    async def _download_images(self, urls: list[str]) -> list[SavedImage]:
        if not urls:
            raise UpstreamError("the page did not produce any generated images")
        saved: list[SavedImage] = []
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    raise UpstreamError(f"failed to download generated image: {exc}") from exc
                ctype = resp.headers.get("content-type", "").lower()
                if "webp" in ctype:
                    ext = ".webp"
                elif "jpeg" in ctype or "jpg" in ctype:
                    ext = ".jpg"
                else:
                    ext = ".png"
                path = new_artifact_path(ext)
                path.write_bytes(resp.content)
                saved.append(SavedImage(name=path.name, path=path))
        return saved

    async def _save_music(self, url: str) -> SavedImage:
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"failed to download generated music: {exc}") from exc
        ctype = resp.headers.get("content-type", "").lower()
        body = resp.content
        if body[:4] == b"fLaC" or "flac" in ctype:
            ext = ".flac"
        elif body[:4] == b"RIFF" or "wav" in ctype:
            ext = ".wav"
        else:
            ext = ".mp3"
        path = new_artifact_path(ext)
        path.write_bytes(body)
        return SavedImage(name=path.name, path=path)

    def _image_markdown(self, images: list[SavedImage]) -> str:
        return "\n\n".join(
            f"![generated image {i}]({file_url(self.settings, image.name)})"
            for i, image in enumerate(images, 1)
        )

    async def stream_run(self, res: Resolution, spec: ModelSpec) -> AsyncIterator[str]:
        if res.adopt_pairs:
            for role, content in res.adopt_pairs:
                await self.db.append_history(res.conv_id, role, content)
            await self.db.touch(res.conv_id)
            yield res.cached_reply or ""
            return
        if res.mode == "repeat":
            yield res.cached_reply or ""
            return

        waited_ms = await self.queue.acquire()
        if waited_ms > 1000:
            logger.info("stream %s waited %dms in the queue", res.conv_id, waited_ms)
        try:
            await self.bm.ensure_available()
            if spec.kind == "image":
                async for chunk in self._stream_image(res, spec):
                    yield chunk
                return
            if spec.kind == "music":
                async for chunk in self._stream_music(res, spec):
                    yield chunk
                return
            if spec.agent == "ppt":
                async for chunk in self._stream_ppt(res, spec):
                    yield chunk
                return
            async for chunk in self._stream_chat(res, spec):
                yield chunk
        finally:
            self.queue.release()

    async def _stream_chat(self, res: Resolution, spec: ModelSpec) -> AsyncIterator[str]:
        pre = await self._navigate_and_send(res, spec)
        last = ""
        any_yielded = False
        try:
            async for snap in self.driver.reply_stream(pre):
                delta = snap[len(last) :] if snap.startswith(last) else snap
                last = snap
                if delta:
                    any_yielded = True
                    yield delta
            if not any_yielded:
                logger.warning("first-token timeout: returning empty reply for %s", res.conv_id)
            await self._finalize(res, last, spec)
        except (asyncio.CancelledError, GeneratorExit):
            final = await self.driver.drain(pre)
            await self._finalize(res, final, spec)
            raise

    async def _stream_image(self, res: Resolution, spec: ModelSpec) -> AsyncIterator[str]:
        pre = await self._navigate_and_send(res, spec)
        try:
            urls = await self.driver.wait_images(pre)
            saved = await self._save_generated_images(urls)
            content = self._image_markdown(saved)
            await self._finalize(res, content, spec)
            yield content
        except (asyncio.CancelledError, GeneratorExit):
            with suppress(Exception):
                urls = await self.driver.wait_images(pre)
                saved = await self._save_generated_images(urls)
                await self._finalize(res, self._image_markdown(saved), spec)
            raise

    async def _stream_music(self, res: Resolution, spec: ModelSpec) -> AsyncIterator[str]:
        pre = await self._navigate_and_send(res, spec)
        try:
            urls = await self.driver.wait_music(pre)
            clip = await self._save_music(urls[0])
            content = f"[generated music]({file_url(self.settings, clip.name)})"
            await self._finalize(res, content, spec)
            yield content
        except (asyncio.CancelledError, GeneratorExit):
            with suppress(Exception):
                urls = await self.driver.wait_music(pre)
                clip = await self._save_music(urls[0])
                await self._finalize(
                    res, f"[generated music]({file_url(self.settings, clip.name)})", spec
                )
            raise

    async def _stream_ppt(self, res: Resolution, spec: ModelSpec) -> AsyncIterator[str]:
        pre = await self._navigate_and_send(res, spec)
        last = ""
        try:
            async for snap in self.driver.reply_stream(pre):
                delta = snap[len(last) :] if snap.startswith(last) else snap
                last = snap
                if delta:
                    yield delta
            content = await self._build_ppt_content(last, spec, res)
            link = content[len(last) :] if content.startswith(last) else content
            if link:
                yield link
        except (asyncio.CancelledError, GeneratorExit):
            with suppress(Exception):
                summary = await self.driver.drain(pre)
                await self._build_ppt_content(summary, spec, res)
            raise

    async def _build_ppt_content(
        self, summary: str, spec: ModelSpec, res: Resolution
    ) -> str:
        target = new_artifact_path(".pptx")
        try:
            original = await self.driver.download_ppt(target)
        except Exception as exc:
            await self._finalize(res, f"{summary}\n\n[PPT download failed]".strip(), spec)
            if isinstance(exc, UpstreamError):
                raise
            raise UpstreamError(f"failed to download generated PPT: {exc}") from exc
        link = f"[{original}]({file_url(self.settings, target.name)})"
        content = f"{summary}\n\n{link}".strip() if summary else link
        await self._finalize(res, content, spec)
        return content
