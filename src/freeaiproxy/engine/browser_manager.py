from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from camoufox.async_api import AsyncCamoufox
from playwright.async_api import BrowserContext, Page

from freeaiproxy.config import Settings, browser_headless
from freeaiproxy.engine.errors import NotLoggedInError, UpstreamPageChangedError

logger = logging.getLogger("freeaiproxy.browser")


class LoginWizardTimeout(Exception):
    pass


@dataclass(frozen=True)
class LoginSpec:
    name: str
    startup_url: str
    profile_path: Path
    login_hints: tuple[str, ...] = ()
    login_surfaces: tuple[str, ...] = ()
    logged_in_selectors: tuple[str, ...] = ()
    # Browser locale drives the site UI language the selectors target. English
    # sites (DeepSeek/Yuanbao) are calibrated against en-US; a Chinese-only site
    # like Wenxin needs zh-CN. Defaults to en-US when unset.
    locale: str = "en-US"


def _host_os() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# Runtime state a crashed browser leaves behind. Firefox refuses to start a
# profile that still carries a lock or a half-written session checkpoint, so
# these are removed before every launch. The login state itself lives in
# cookies.sqlite / storage/, which are never touched.
_STALE_RUNTIME_FILES = ("parent.lock", "lock", ".parentlock", "sessionCheckpoints.json")
_STALE_RUNTIME_DIRS = ("sessionstore-backups", "sessionstore-backups-recovery")

# Everything a Camoufox profile needs to carry the DeepSeek session. A profile
# that also holds extension bookkeeping (extension-store, extensions.json,
# addonStartup.json.lz4, ...) can refuse to start after the browser was
# upgraded or killed, because that state belongs to a different build. The
# login state itself only needs the cookie database, local storage and prefs,
# so a profile that fails to launch is rebuilt from exactly these files.
_PROFILE_ESSENTIAL_FILES = ("cookies.sqlite", "prefs.js", "times.json", "compatibility.ini")
_PROFILE_ESSENTIAL_DIRS = ("storage",)


def _clean_stale_runtime_state(profile_dir: Path) -> None:
    for name in _STALE_RUNTIME_FILES:
        p = profile_dir / name
        if p.exists():
            with contextlib.suppress(OSError):
                p.unlink()
    for name in _STALE_RUNTIME_DIRS:
        p = profile_dir / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def _profile_in_use(profile_dir: Path) -> bool:
    """Whether a live browser currently holds the profile.

    Firefox keeps parent.lock open for as long as it runs, so renaming the
    file fails while the browser is alive and succeeds once it is gone.
    """
    lock = profile_dir / "parent.lock"
    if not lock.exists():
        return False
    probe = lock.with_name("parent.lock.probe")
    try:
        lock.replace(probe)
        probe.replace(lock)
        return False
    except OSError:
        with contextlib.suppress(OSError):
            if probe.exists():
                probe.replace(lock)
        return True


def _rebuild_minimal_profile(profile_dir: Path) -> bool:
    """Rebuild the profile from the files that carry the login state.

    The old directory is kept aside until the new one is in place, so a
    failure halfway through does not leave the gateway without a profile.
    """
    staging = profile_dir.with_name(profile_dir.name + ".rebuild")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name in _PROFILE_ESSENTIAL_FILES:
        src = profile_dir / name
        if src.is_file():
            shutil.copy2(src, staging / name)
    for name in _PROFILE_ESSENTIAL_DIRS:
        src = profile_dir / name
        if src.is_dir():
            shutil.copytree(src, staging / name, dirs_exist_ok=True)
    backup = profile_dir.with_name(profile_dir.name + ".broken")
    shutil.rmtree(backup, ignore_errors=True)
    shutil.move(str(profile_dir), str(backup))
    shutil.move(str(staging), str(profile_dir))
    shutil.rmtree(backup, ignore_errors=True)
    return True


class BrowserManager:
    def __init__(self, settings: Settings, login: LoginSpec):
        self.settings = settings
        self.login = login
        self._cm: AsyncCamoufox | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None
        self.logged_in = False
        self.state = "stopped"
        self.headless: bool | None = None
        self.login_waiting = False
        self._default_headless = browser_headless(settings)
        self._lifecycle = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle:
            await self._start_locked()

    async def _start_locked(self, *, wait_for_login: bool = True) -> None:
        await self._launch()
        await self._navigate_base()
        if await self.check_login_state():
            self.logged_in = True
            self.state = "ready"
            return
        self.state = "login_required"
        await self._prepare_login_locked(wait_for_login)

    async def _prepare_login_locked(self, wait_for_login: bool) -> None:
        if self._default_headless and self.headless:
            logger.info("%s login required; opening a headed browser", self.login.name)
            await self._relaunch_locked(headless=False)
            if await self.check_login_state():
                self.logged_in = True
                self.state = "ready"
                if await self._switch_to_default_mode_locked():
                    return
                self.state = "login_required"
        if not wait_for_login:
            return
        await self.run_login_wizard()
        if self.logged_in:
            await self._switch_to_default_mode_locked()

    async def _switch_to_default_mode_locked(self) -> bool:
        if self.headless == self._default_headless:
            return self.logged_in
        await self._relaunch_locked(headless=self._default_headless)
        if await self.check_login_state():
            self.logged_in = True
            self.state = "ready"
            return True
        self.logged_in = False
        self.state = "login_required"
        if self.headless:
            await self._relaunch_locked(headless=False)
        return False

    async def _relaunch_locked(self, *, headless: bool) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._stop_locked(), timeout=15.0)
        await self._launch(headless=headless)
        await self._navigate_base()

    async def _ensure_headed_for_login_locked(self) -> None:
        if not self._default_headless or not self.headless:
            return
        logger.info("%s login required; opening a headed browser", self.login.name)
        await self._relaunch_locked(headless=False)
        self.logged_in = False
        self.state = "login_required"

    def is_running(self) -> bool:
        return (
            self._cm is not None
            and self._context is not None
            and self.page is not None
            and not self.page.is_closed()
        )

    async def ensure_available(self) -> None:
        """Make sure this provider's browser can serve a request.

        A closed, crashed, or unresponsive browser is replaced on the spot; the
        persisted profile normally carries the login state into the new one.
        """
        async with self._lifecycle:
            if self.is_running() and await self._responsive():
                if self.state != "ready":
                    if await self.check_login_state():
                        self.logged_in = True
                        self.state = "ready"
                        if not await self._switch_to_default_mode_locked():
                            raise NotLoggedInError(
                                f"{self.login.name} login was not preserved when "
                                "switching to headless mode; the browser was reopened "
                                "for sign-in"
                            )
                        return
                    await self._ensure_headed_for_login_locked()
                    raise NotLoggedInError(
                        f"{self.login.name} requires login; complete it in the "
                        "opened browser window and retry"
                    )
                return
            if self.is_running():
                logger.warning("%s browser is not responding; restarting it", self.login.name)
            else:
                logger.warning("%s browser is not running; starting a new one", self.login.name)
            await self._recover_locked()

    async def _responsive(self, timeout_s: float = 5.0) -> bool:
        page = self.page
        if page is None:
            return False
        try:
            await asyncio.wait_for(page.evaluate("() => true"), timeout=timeout_s)
            return True
        except Exception:
            return False

    async def _recover_locked(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._stop_locked(), timeout=15.0)
        try:
            await self._start_locked(wait_for_login=False)
        except Exception as exc:
            if isinstance(exc, UpstreamPageChangedError):
                raise
            raise UpstreamPageChangedError(
                f"failed to start a new {self.login.name} browser: {exc}"
            ) from exc
        if not self.logged_in:
            raise NotLoggedInError(
                f"{self.login.name} browser was restarted but is not logged in; "
                "complete login in the opened window and retry"
            )

    async def probe(self) -> bool:
        """最小启动：仅检测登录态，不强制进入登录向导。"""
        await self._launch()
        await self._navigate_base()
        assert self.page is not None
        try:
            await asyncio.sleep(1.5)
            self.logged_in = await self.check_login_state()
        finally:
            if self.logged_in:
                self.state = "ready"
        return self.logged_in

    async def _launch(self, headless: bool | None = None) -> None:
        mode = self._default_headless if headless is None else headless
        profile_dir = self.login.profile_path
        profile_dir.mkdir(parents=True, exist_ok=True)
        _clean_stale_runtime_state(profile_dir)
        kwargs: dict = {
            "persistent_context": True,
            "user_data_dir": str(profile_dir),
            "humanize": True,
            "os": _host_os(),
            "locale": self.login.locale,
            "headless": mode,
        }
        if self.settings.debug.slowmo_ms > 0:
            kwargs["slow_mo"] = self.settings.debug.slowmo_ms

        self.state = "starting"
        self.headless = mode
        self._cm = AsyncCamoufox(**kwargs)
        context = await self._launch_context(mode)
        if not isinstance(context, BrowserContext):
            await self._cm.__aexit__(None, None, None)
            self._cm = None
            raise RuntimeError("camoufox did not return a persistent browser context")
        self._context = context
        if self.settings.debug.trace:
            trace_dir = Path("data/traces") / self.login.name
            trace_dir.mkdir(parents=True, exist_ok=True)
            await self._context.tracing.start(snapshots=True, screenshots=True, sources=True)

        self.page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    async def _launch_context(self, headless: bool):
        """Enter the browser, rebuilding a broken profile once if needed."""
        cm = self._cm
        if cm is None:
            raise RuntimeError("browser context manager is not initialised")
        try:
            return await cm.__aenter__()
        except Exception as exc:
            profile_dir = self.login.profile_path
            if _profile_in_use(profile_dir):
                raise UpstreamPageChangedError(
                    "the browser profile is in use by another process; "
                    "stop the other instance before starting the gateway"
                ) from exc
            print(
                f"[FreeAiProxy] {self.login.name} 浏览器无法使用当前 profile"
                f"（{type(exc).__name__}），正在以最小 profile（保留登录态）重建后重试。"
            )
            _rebuild_minimal_profile(profile_dir)
            _clean_stale_runtime_state(profile_dir)
            self._cm = AsyncCamoufox(
                persistent_context=True,
                user_data_dir=str(profile_dir),
                humanize=True,
                os=_host_os(),
                locale=self.login.locale,
                headless=headless,
            )
            return await self._cm.__aenter__()

    async def run_login_wizard(self) -> None:
        assert self.page is not None
        self.login_waiting = True
        print("!" * 60)
        print(f"[FreeAiProxy] {self.login.name} 检测到未登录。")
        print("[FreeAiProxy] 请在自动弹出的浏览器窗口中完成登录（手机号/邮箱/验证码/扫码均可）。")
        print("[FreeAiProxy] 登录成功后本进程会自动检测并继续启动，无需其他操作。")
        print("!" * 60)
        try:
            deadline = time.monotonic() + 30 * 60
            cycle = 0
            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                cycle += 1
                if cycle % 15 == 0:
                    await self._navigate_base()
                if await self.check_login_state():
                    self.logged_in = True
                    self.state = "ready"
                    print(f"[FreeAiProxy] {self.login.name} 登录成功，服务就绪。")
                    return
            raise LoginWizardTimeout("user did not complete login within 30 minutes")
        finally:
            self.login_waiting = False

    async def _navigate_base(self) -> None:
        assert self.page is not None
        with contextlib.suppress(Exception):
            await self.page.goto(self.login.startup_url, wait_until="domcontentloaded")

    async def check_login_state(self) -> bool:
        page = self.page
        if page is None:
            return False
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        await asyncio.sleep(0.8)
        for sel in self.login.login_surfaces:
            try:
                loc = page.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    return False
            except Exception:
                continue
        for text in self.login.login_hints:
            if await self._hint_present(text):
                return False
        for sel in self.login.logged_in_selectors:
            loc = page.locator(sel)
            try:
                if await loc.count() and await loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _hint_present(self, text: str) -> bool:
        page = self.page
        if page is None:
            return False
        try:
            # A DeepSeek page carries thousands of nodes; an unbounded
            # get_by_text scan can take seconds, which would stall the
            # startup check, so the scan is time-bounded.
            found = page.get_by_text(text, exact=False).first.count()
            return bool(await asyncio.wait_for(found, timeout=3.0))
        except Exception:
            return False

    async def hint_present(self, text: str) -> bool:
        return await self._hint_present(text)

    async def any_login_hint(self) -> bool:
        page = self.page
        if page is None:
            return False
        for sel in self.login.login_surfaces:
            try:
                loc = page.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    return True
            except Exception:
                continue
        for text in self.login.login_hints:
            if await self._hint_present(text):
                return True
        return False

    def mark_logged_out(self) -> None:
        self.logged_in = False
        if self.state == "ready":
            self.state = "login_required"

    async def relogin(self) -> None:
        async with self._lifecycle:
            self.logged_in = False
            self.state = "login_required"
            if not self.is_running():
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._stop_locked(), timeout=15.0)
                await self._start_locked()
                return
            if self._default_headless and self.headless:
                await self._relaunch_locked(headless=False)
            await self._navigate_base()
            await self.run_login_wizard()
            if self.logged_in:
                await self._switch_to_default_mode_locked()

    async def stop(self) -> None:
        async with self._lifecycle:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        try:
            if self._context is not None and self.settings.debug.trace:
                try:
                    trace_dir = Path("data/traces") / self.login.name
                    trace_dir.mkdir(parents=True, exist_ok=True)
                    await self._context.tracing.stop(
                        path=str(trace_dir / f"trace-{int(time.time())}.zip")
                    )
                except Exception:
                    pass
            if self._cm is not None:
                with contextlib.suppress(Exception):
                    await self._cm.__aexit__(None, None, None)
        finally:
            self._cm = None
            self._context = None
            self.page = None
            self.state = "stopped"
            self.logged_in = False
            self.headless = None
