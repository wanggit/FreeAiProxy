from __future__ import annotations

import asyncio
import configparser
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

import httpx
import psutil
import typer
from rich.console import Console

from freeaiproxy.config import (
    Settings,
    browser_headless,
    enabled_provider_names,
    load_settings,
    provider_profile_path,
)
from freeaiproxy.services.provider_runtime import make_browser_manager

app = typer.Typer(help="FreeAiProxy — DeepSeek / 腾讯元宝 网页版 → OpenAI 兼容 API 网关")
profile_app = typer.Typer(help="登录态 profile 导入/同步/导出（仅支持 Firefox 系）")
app.add_typer(profile_app, name="profile")

console = Console()

FIREFOX_PROC_NAMES = {"firefox", "firefox.exe"}


def _settings(config: Path | None) -> Settings:
    return load_settings(config)


def _profile_target(config: Path | None, provider: str = "deepseek") -> Path:
    return provider_profile_path(_settings(config), provider)


def _resolve_providers(settings: Settings, provider: str) -> list[str]:
    enabled = enabled_provider_names(settings)
    if provider == "all":
        return enabled
    if provider not in ("deepseek", "yuanbao"):
        raise typer.BadParameter(f"unknown provider '{provider}'")
    if provider not in enabled:
        raise typer.BadParameter(f"provider '{provider}' is not enabled")
    return [provider]


@app.command()
def start(config: Path | None = typer.Option(None, "--config", help="配置文件路径")):
    """启动网关服务（无登录态时会弹出浏览器引导登录）。"""
    settings = _settings(config)
    console.print(f"[green]FreeAiProxy[/] 启动于 http://{settings.server.host}:{settings.server.port}")
    if browser_headless(settings):
        console.print("[dim]无头模式；遇到未登录时会临时打开有头窗口供登录。[/]")
    import uvicorn

    from freeaiproxy.app import create_app

    uvicorn.run(create_app(config), host=settings.server.host, port=settings.server.port)


@app.command()
def status(
    config: Path | None = typer.Option(None, "--config", help="配置文件路径"),
    provider: str = typer.Option("all", "--provider", help="deepseek / yuanbao / all"),
):
    """检测当前 profile 的登录态。"""
    settings = _settings(config)
    names = _resolve_providers(settings, provider)
    if _print_running_status(settings, names):
        return
    for name in names:
        console.print(f"[cyan]== {name} 登录态检测 ==[/]")
        asyncio.run(_probe_login(settings, name))


def _print_running_status(settings: Settings, names: list[str]) -> bool:
    base = f"http://{settings.server.host}:{settings.server.port}"
    try:
        resp = httpx.get(f"{base}/healthz", timeout=3.0)
        if resp.status_code != 200:
            return False
        providers = resp.json().get("providers") or {}
    except Exception:
        return False
    if not providers:
        return False
    for name in names:
        state = providers.get(name)
        if state is None:
            return False
        if state.get("logged_in"):
            console.print(f"[green]{name} 登录态有效[/] (state={state.get('state')})")
        else:
            console.print(
                f"[red]{name} 未登录[/] (state={state.get('state')})。"
                f"请运行 [cyan]freeaiproxy relogin --provider {name}[/]"
            )
    return True


async def _probe_login(settings: Settings, provider: str) -> None:
    bm = make_browser_manager(settings, provider)
    console.print(f"[cyan]启动 {provider} 浏览器检测登录态...[/]")
    try:
        logged = await bm.probe()
    finally:
        await bm.stop()
    if logged:
        console.print(f"[green]{provider} 登录态有效[/]")
    else:
        console.print(
            f"[red]{provider} 未登录[/]。请运行 "
            f"[cyan]freeaiproxy relogin --provider {provider}[/] 或导入 Firefox 登录态："
            f"[cyan]freeaiproxy profile import --browser firefox --provider {provider}[/]"
        )


@app.command()
def relogin(
    config: Path | None = typer.Option(None, "--config", help="配置文件路径"),
    provider: str = typer.Option("deepseek", "--provider", help="deepseek / yuanbao / all"),
):
    """触发重新登录（服务在跑则走 admin 端点；未运行则独立引导）。"""
    settings = _settings(config)
    for name in _resolve_providers(settings, provider):
        _relogin_provider(settings, name)


def _relogin_provider(settings: Settings, provider: str) -> None:
    base = f"http://{settings.server.host}:{settings.server.port}"
    try:
        resp = httpx.post(
            f"{base}/admin/relogin",
            params={"provider": provider},
            headers={"Authorization": f"Bearer {settings.server.api_key}"}
            if settings.server.api_key
            else None,
            timeout=10.0,
        )
        if resp.status_code in (200, 202):
            console.print(
                f"[green]已在运行中的服务上触发 {provider} 重新登录[/]，"
                "请前往浏览器窗口完成登录。"
            )
            return
        console.print(f"[yellow]服务响应 {resp.status_code}，改为独立引导。[/]")
    except Exception:
        console.print(f"[yellow]服务未在运行，独立启动 {provider} 浏览器进行登录引导...[/]")
    asyncio.run(_standalone_relogin(settings, provider))


async def _standalone_relogin(settings: Settings, provider: str) -> None:
    bm = make_browser_manager(settings, provider)
    try:
        await bm.start()
    finally:
        await bm.stop()


@profile_app.command("import")
def profile_import(
    browser: str | None = typer.Option(None, "--browser", help="源浏览器：firefox"),
    path: Path | None = typer.Option(None, "--path", help="手动指定 Firefox profile 目录"),
    config: Path | None = typer.Option(None, "--config", help="配置文件路径"),
    provider: str = typer.Option("deepseek", "--provider", help="deepseek / yuanbao"),
):
    """从 Firefox 导入登录态到目标 provider profile（覆盖式）。"""
    _profile_transfer("import", browser, path, config, provider)


@profile_app.command("sync")
def profile_sync(
    browser: str | None = typer.Option(None, "--browser", help="源浏览器：firefox"),
    path: Path | None = typer.Option(None, "--path", help="手动指定 Firefox profile 目录"),
    config: Path | None = typer.Option(None, "--config", help="配置文件路径"),
    provider: str = typer.Option("deepseek", "--provider", help="deepseek / yuanbao"),
):
    """从 Firefox 覆盖式重新同步登录态。"""
    _profile_transfer("sync", browser, path, config, provider)


@profile_app.command("export")
def profile_export(
    out: Path | None = typer.Option(None, "--out", help="输出的 zip 路径"),
    config: Path | None = typer.Option(None, "--config"),
    provider: str = typer.Option("deepseek", "--provider", help="deepseek / yuanbao"),
):
    """打包当前 provider profile 为 zip，用于迁移到其他机器。"""
    _profile_export(out, config, provider)


def _is_live_firefox_profile(path: Path) -> bool:
    """Whether the source sits inside a real Firefox profile tree.

    Only such a source needs Firefox to be closed: its cookie database is
    locked by the running browser. An arbitrary --path (a copy, an export, a
    profile on another disk) is not locked by a running Firefox.
    """
    for parent in path.parents:
        if parent.name.lower() == "profiles":
            root = parent.parent
            return "firefox" in root.name.lower() or "mozilla" in str(root).lower()
    return False


def _profile_transfer(
    mode: str, browser: str | None, path: Path | None, config: Path | None, provider: str
) -> None:
    if provider not in ("deepseek", "yuanbao"):
        console.print(f"[red]未知 provider '{provider}'[/]")
        raise typer.Exit(1)
    if browser and browser.lower() != "firefox":
        console.print(
            f"[red]仅支持 firefox。'{browser}' 是 Chromium 内核（cookie 加密与 profile 结构不兼容），"
            "无法导入到 Camoufox；请在 Firefox 里登录后重试，或用 [cyan]freeaiproxy relogin[/] 手动登录。[/]"
        )
        raise typer.Exit(1)
    if not browser and not path:
        console.print("[red]必须指定 --browser firefox 或 --path <profile 目录>[/]")
        raise typer.Exit(1)

    source = path if path else _locate_firefox_profile()
    if source is None:
        console.print("[red]未能定位 Firefox profile 目录，请用 --path 手动指定[/]")
        raise typer.Exit(1)
    source = Path(source).resolve()
    _assert_firefox_profile(source)
    console.print(f"源 profile:{source}")
    console.print("[yellow]提醒：若 freeaiproxy 服务正在运行，请先停止再执行导入。[/]")
    if _is_live_firefox_profile(source):
        _wait_for_firefox_closed()
    else:
        console.print("[cyan]源目录不是正在使用的 Firefox profile 目录，跳过运行状态检查。[/]")
    target = _profile_target(config, provider)
    if not typer.confirm(
        f"覆盖 {provider} {('重新同步' if mode == 'sync' else '导入')}目标 {target}，继续?",
        default=True,
    ):
        raise typer.Exit(0)

    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    console.print(f"[green]{'同步' if mode == 'sync' else '导入'}完成：[/]{source} → {target}")
    console.print(f"验证：[cyan]freeaiproxy status --provider {provider}[/]")


def _assert_firefox_profile(p: Path) -> None:
    if not (p / "cookies.sqlite").exists():
        if (p / "places.sqlite").exists() or (p / "prefs.js").exists():
            console.print(
                f"[yellow]{p} 看起来是 Firefox profile，但没有 cookies.sqlite"
                "（该 profile 可能从未登录过任何网站）。[/]"
            )
        else:
            console.print(f"[red]{p} 不是 Firefox profile 目录（缺少 cookies.sqlite / places.sqlite）[/]")
        raise typer.Exit(1)


def browser_pids() -> list[int]:
    pids: list[int] = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info["name"] or "").lower()
        except Exception:
            continue
        if name in FIREFOX_PROC_NAMES:
            pids.append(proc.pid)
    return pids


def _wait_for_firefox_closed() -> None:
    while True:
        pids = browser_pids()
        if not pids:
            return
        console.print(f"[yellow]检测到 Firefox 正在运行（PID {', '.join(map(str, pids))}）。[/]")
        console.print("Firefox 运行时会锁定 cookies.sqlite / places.sqlite，强行复制可能得到损坏数据。")
        try:
            input("请关闭 Firefox，然后按回车重试（Ctrl+C 取消）...")
        except (KeyboardInterrupt, EOFError) as exc:
            console.print("[yellow]已取消。[/]")
            raise typer.Exit(1) from exc


def _firefox_roots() -> list[Path]:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
        return [base] if base.exists() else []
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Firefox"
        return [base] if base.exists() else []
    bases = [
        Path.home() / ".mozilla" / "firefox",
        Path.home() / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        Path.home() / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
    ]
    return [b for b in bases if b.exists()]


def _locate_firefox_profile() -> Path | None:
    for base in _firefox_roots():
        ini = base / "profiles.ini"
        if ini.is_file():
            found = _default_from_profiles_ini(base, ini)
            if found:
                return found
        profiles_dir = base / "Profiles"
        if profiles_dir.is_dir():
            dirs = [d for d in profiles_dir.glob("*.default*") if d.is_dir()]
            if dirs:
                return max(dirs, key=lambda d: d.stat().st_mtime)
        dirs = [d for d in base.glob("*.default*") if d.is_dir()]
        if dirs:
            return max(dirs, key=lambda d: d.stat().st_mtime)
    return None


def _default_from_profiles_ini(base: Path, ini: Path) -> Path | None:
    """Pick the profile Firefox marks as default.

    Modern Firefox records the default profile in the [Install*] section as
    `Default=Profiles/<name>` (no `Path` key), while older layouts use a
    `Default=1` flag inside the [Profile*] section. Both are honoured, and a
    profile only counts as usable when it actually carries a cookie database.
    """
    try:
        cp = configparser.ConfigParser()
        cp.read(ini, encoding="utf-8")
    except Exception:
        return None
    install_default: str | None = None
    flagged: str | None = None
    fallback: str | None = None
    for name in cp.sections():
        if not cp.has_option(name, "Path"):
            if name.startswith("Install") and cp.has_option(name, "Default"):
                value = cp.get(name, "Default", fallback="").strip()
                if value:
                    install_default = value
            continue
        path = cp.get(name, "Path", fallback="").strip()
        if not path:
            continue
        if cp.get(name, "Default", fallback="0").strip().lower() in ("1", "true"):
            flagged = path
        elif fallback is None:
            fallback = path
    for candidate in (install_default, flagged, fallback):
        if not candidate:
            continue
        full = Path(candidate) if Path(candidate).is_absolute() else base / candidate
        if full.is_dir() and (full / "cookies.sqlite").exists():
            return full
    for candidate in (install_default, flagged, fallback):
        if not candidate:
            continue
        full = Path(candidate) if Path(candidate).is_absolute() else base / candidate
        if full.is_dir():
            return full
    return None


_EXCLUDE_DIRS = {"cache2", "startCache", "shader-cache", "thumbnails", "crashes", "gpu-cache"}


def _profile_export(out: Path | None, config: Path | None, provider: str) -> None:
    if provider not in ("deepseek", "yuanbao"):
        console.print(f"[red]未知 provider '{provider}'[/]")
        raise typer.Exit(1)
    src = _profile_target(config, provider)
    if not src.is_dir():
        console.print(f"[red]{src} 不存在，无可导出的登录态[/]")
        raise typer.Exit(1)
    if out is None:
        export_dir = Path("data") / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        out = export_dir / (
            f"freeaiproxy-{provider}-profile-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    excluded = {d.resolve() for d in src.rglob("*") if d.is_dir() and d.name in _EXCLUDE_DIRS}
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            rp = f.resolve()
            if any(rp.is_relative_to(d) for d in excluded):
                continue
            zf.write(f, arcname=str(f.relative_to(src)))
            count += 1
    console.print(f"[green]已导出 {count} 个文件 →[/] {out}")


def main() -> None:
    if not sys.stdout.isatty() and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not sys.stderr.isatty() and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    app()


if __name__ == "__main__":
    main()
