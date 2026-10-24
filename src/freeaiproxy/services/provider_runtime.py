from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from freeaiproxy.config import Settings, provider_profile_path
from freeaiproxy.db import Database
from freeaiproxy.engine import chatglm_selectors as CG
from freeaiproxy.engine import doubao_selectors as DB
from freeaiproxy.engine import selectors as DS
from freeaiproxy.engine import wenxin_selectors as WX
from freeaiproxy.engine import yuanbao_selectors as YB
from freeaiproxy.engine.browser_manager import BrowserManager, LoginSpec
from freeaiproxy.services.chat_service import ChatService
from freeaiproxy.services.queue import RequestQueue


@dataclass
class ProviderRuntime:
    name: str
    bm: BrowserManager
    driver: Any
    service: ChatService


def login_spec(settings: Settings, name: str) -> LoginSpec:
    profile_path = provider_profile_path(settings, name)
    if name == "deepseek":
        return LoginSpec(
            name="deepseek",
            startup_url=DS.CHAT_BASE_URL,
            profile_path=profile_path,
            login_hints=tuple(DS.LOGIN_TEXT_HINTS),
            logged_in_selectors=tuple(DS.LOGGED_IN_CANDIDATES),
        )
    if name == "yuanbao":
        return LoginSpec(
            name="yuanbao",
            startup_url=YB.BASE_URL,
            profile_path=profile_path,
            login_hints=YB.LOGIN_HINTS,
            login_surfaces=YB.LOGIN_SURFACES,
            logged_in_selectors=YB.LOGGED_IN_SELECTORS,
        )
    if name == "wenxin":
        return LoginSpec(
            name="wenxin",
            startup_url=WX.BASE_URL,
            profile_path=profile_path,
            login_hints=WX.LOGIN_HINTS,
            login_surfaces=WX.LOGIN_SURFACES,
            logged_in_selectors=WX.LOGGED_IN_SELECTORS,
            locale="zh-CN",
        )
    if name == "chatglm":
        return LoginSpec(
            name="chatglm",
            startup_url=CG.CHAT_URL,
            profile_path=profile_path,
            login_hints=CG.LOGIN_HINTS,
            login_surfaces=CG.LOGIN_SURFACES,
            logged_in_selectors=CG.LOGGED_IN_SELECTORS,
            locale="zh-CN",
        )
    if name == "doubao":
        return LoginSpec(
            name="doubao",
            startup_url=DB.BASE_URL,
            profile_path=profile_path,
            login_hints=DB.LOGIN_HINTS,
            login_surfaces=(DB.LOGIN_BUTTON,),
            logged_in_selectors=tuple(DB.LOGGED_IN_SELECTORS),
            locale="zh-CN",
        )
    raise ValueError(f"unknown provider '{name}'")


def make_browser_manager(settings: Settings, name: str) -> BrowserManager:
    return BrowserManager(settings, login_spec(settings, name))


def make_driver(settings: Settings, name: str, bm: BrowserManager) -> Any:
    if name == "deepseek":
        from freeaiproxy.engine.ds_driver import DeepSeekDriver

        return DeepSeekDriver(bm, settings)
    if name == "yuanbao":
        from freeaiproxy.engine.yuanbao_driver import YuanbaoDriver

        return YuanbaoDriver(bm, settings)
    if name == "wenxin":
        from freeaiproxy.engine.wenxin_driver import WenxinDriver

        return WenxinDriver(bm, settings)
    if name == "chatglm":
        from freeaiproxy.engine.chatglm_driver import ChatGLMDriver

        return ChatGLMDriver(bm, settings)
    if name == "doubao":
        from freeaiproxy.engine.doubao_driver import DoubaoDriver

        return DoubaoDriver(bm, settings)
    raise ValueError(f"unknown provider '{name}'")


def create_runtime(settings: Settings, name: str, db: Database) -> ProviderRuntime:
    bm = make_browser_manager(settings, name)
    driver = make_driver(settings, name, bm)
    queue = RequestQueue(max_len=settings.worker.max_queue_len)
    service = ChatService(settings, db, bm, driver, queue, name)
    return ProviderRuntime(name=name, bm=bm, driver=driver, service=service)
