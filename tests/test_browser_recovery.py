from __future__ import annotations

from pathlib import Path

import pytest

from freeaiproxy.config import DebugConfig, Settings, WorkerConfig, browser_headless
from freeaiproxy.engine.browser_manager import BrowserManager, LoginSpec
from freeaiproxy.engine.errors import NotLoggedInError


class _Page:
    def __init__(self, closed: bool = False) -> None:
        self.closed = closed

    def is_closed(self) -> bool:
        return self.closed

    async def evaluate(self, _script: str) -> bool:
        return True


def _manager(*, headless: bool = False) -> BrowserManager:
    return BrowserManager(
        Settings(debug=DebugConfig(headed=not headless)),
        LoginSpec("test", "https://example.com", Path("data/test-profile")),
    )


def test_browser_mode_precedence() -> None:
    assert browser_headless(Settings(debug=DebugConfig(headed=True))) is False
    assert browser_headless(Settings(debug=DebugConfig(headed=False))) is True
    assert (
        browser_headless(
            Settings(debug=DebugConfig(headed=None), worker=WorkerConfig(headless=True))
        )
        is True
    )
    assert (
        browser_headless(
            Settings(debug=DebugConfig(headed=None), worker=WorkerConfig(headless=False))
        )
        is False
    )


def _attach(bm: BrowserManager, page: _Page, *, headless: bool) -> None:
    bm._cm = object()
    bm._context = object()
    bm.page = page
    bm.headless = headless
    bm.state = "ready"
    bm.logged_in = True


async def test_ensure_available_keeps_a_healthy_browser(monkeypatch) -> None:
    bm = _manager()
    _attach(bm, _Page(), headless=False)

    async def recover() -> None:
        raise AssertionError("a healthy browser must not be restarted")

    monkeypatch.setattr(bm, "_recover_locked", recover)
    await bm.ensure_available()


async def test_ensure_available_recovers_a_closed_page(monkeypatch) -> None:
    bm = _manager()
    _attach(bm, _Page(closed=True), headless=False)
    recovered: list[str] = []

    async def recover() -> None:
        recovered.append("yes")

    monkeypatch.setattr(bm, "_recover_locked", recover)
    await bm.ensure_available()
    assert recovered == ["yes"]


async def test_ensure_available_recovers_an_unresponsive_page(monkeypatch) -> None:
    bm = _manager()
    _attach(bm, _Page(), headless=False)
    recovered: list[str] = []

    async def unresponsive(*_args, **_kwargs) -> bool:
        return False

    async def recover() -> None:
        recovered.append("yes")

    monkeypatch.setattr(bm, "_responsive", unresponsive)
    monkeypatch.setattr(bm, "_recover_locked", recover)
    await bm.ensure_available()
    assert recovered == ["yes"]


async def test_ensure_available_promotes_a_logged_in_session(monkeypatch) -> None:
    bm = _manager()
    _attach(bm, _Page(), headless=False)
    bm.state = "login_required"
    bm.logged_in = False

    async def logged_in() -> bool:
        return True

    monkeypatch.setattr(bm, "check_login_state", logged_in)
    await bm.ensure_available()
    assert bm.state == "ready"
    assert bm.logged_in


async def test_recovery_requires_a_logged_in_profile(monkeypatch) -> None:
    bm = _manager()
    _attach(bm, _Page(closed=True), headless=False)

    async def stop() -> None:
        return None

    async def start(*, wait_for_login: bool = True) -> None:
        bm.state = "login_required"
        bm.logged_in = False

    monkeypatch.setattr(bm, "_stop_locked", stop)
    monkeypatch.setattr(bm, "_start_locked", start)
    with pytest.raises(NotLoggedInError):
        await bm.ensure_available()
    assert bm.state == "login_required"


async def test_headless_login_required_opens_a_headed_browser(monkeypatch) -> None:
    bm = _manager(headless=True)
    _attach(bm, _Page(), headless=True)
    bm.state = "login_required"
    bm.logged_in = False
    modes: list[bool] = []

    async def relaunch(*, headless: bool) -> None:
        modes.append(headless)
        bm.headless = headless

    async def not_logged_in() -> bool:
        return False

    monkeypatch.setattr(bm, "_relaunch_locked", relaunch)
    monkeypatch.setattr(bm, "check_login_state", not_logged_in)
    with pytest.raises(NotLoggedInError):
        await bm.ensure_available()
    assert modes == [False]
    assert bm.state == "login_required"


async def test_headless_promotion_returns_to_headless(monkeypatch) -> None:
    bm = _manager(headless=True)
    _attach(bm, _Page(), headless=False)
    bm.state = "login_required"
    bm.logged_in = False
    modes: list[bool] = []

    async def relaunch(*, headless: bool) -> None:
        modes.append(headless)
        bm.headless = headless

    async def logged_in() -> bool:
        return True

    monkeypatch.setattr(bm, "_relaunch_locked", relaunch)
    monkeypatch.setattr(bm, "check_login_state", logged_in)
    await bm.ensure_available()
    assert modes == [True]
    assert bm.headless is True
    assert bm.state == "ready"


async def test_startup_login_temporarily_uses_a_headed_browser(monkeypatch) -> None:
    bm = _manager(headless=True)
    bm.headless = True
    bm.state = "login_required"
    modes: list[bool] = []
    switched: list[bool] = []

    async def relaunch(*, headless: bool) -> None:
        modes.append(headless)
        bm.headless = headless

    async def not_logged_in() -> bool:
        return False

    async def wizard() -> None:
        bm.logged_in = True
        bm.state = "ready"

    async def switch_back() -> bool:
        switched.append(True)
        return True

    monkeypatch.setattr(bm, "_relaunch_locked", relaunch)
    monkeypatch.setattr(bm, "check_login_state", not_logged_in)
    monkeypatch.setattr(bm, "run_login_wizard", wizard)
    monkeypatch.setattr(bm, "_switch_to_default_mode_locked", switch_back)
    await bm._prepare_login_locked(True)
    assert modes == [False]
    assert switched == [True]


async def test_failed_switch_back_reopens_a_headed_browser(monkeypatch) -> None:
    bm = _manager(headless=True)
    bm.headless = False
    modes: list[bool] = []

    async def relaunch(*, headless: bool) -> None:
        modes.append(headless)
        bm.headless = headless

    async def not_logged_in() -> bool:
        return False

    monkeypatch.setattr(bm, "_relaunch_locked", relaunch)
    monkeypatch.setattr(bm, "check_login_state", not_logged_in)
    assert await bm._switch_to_default_mode_locked() is False
    assert modes == [True, False]
    assert bm.headless is False
    assert bm.state == "login_required"
