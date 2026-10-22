from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    api_key: str = ""


class DebugConfig(BaseModel):
    headed: bool | None = None
    slowmo_ms: int = 0
    trace: bool = False


class WorkerConfig(BaseModel):
    headless: bool = False
    poll_interval_ms: int = 500
    timeout_first_token_s: int = 120
    timeout_completion_s: int = 600
    max_queue_len: int = 20


class ProfileConfig(BaseModel):
    path: str = "data/profile"


class ProviderConfig(BaseModel):
    enabled: bool = True
    profile: str | None = None


class ProvidersConfig(BaseModel):
    deepseek: ProviderConfig = Field(
        default_factory=lambda: ProviderConfig(enabled=True, profile=None)
    )
    yuanbao: ProviderConfig = Field(
        default_factory=lambda: ProviderConfig(
            enabled=False, profile="data/profiles/yuanbao"
        )
    )


class AttachConfig(BaseModel):
    enabled: bool = True
    max_size_mb: int = 20
    max_files: int = 5
    allowed_extensions: list[str] = [
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".svg",
        ".pdf",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".html",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".go",
        ".rs",
        ".yaml",
        ".yml",
        ".ini",
        ".conf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
    ]
    download_remote_urls: bool = True
    remote_timeout_s: int = 30
    remote_max_bytes: int = 20 * 1024 * 1024


class Settings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    attach: AttachConfig = Field(default_factory=AttachConfig)


def browser_headless(settings: Settings) -> bool:
    """Effective browser mode: an explicit debug.headed wins over worker.headless."""
    if settings.debug.headed is not None:
        return not settings.debug.headed
    return settings.worker.headless


def _deep_merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _env_overrides() -> dict:
    out: dict = {}
    prefix = "FREEAIPROXY__"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix) :].lower().split("__")
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def load_settings(config_path: Path | None = None) -> Settings:
    path = config_path or Path("config.toml")
    data: dict = {}
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    merged = _deep_merge(_deep_merge({}, data), _env_overrides())
    return Settings.model_validate(merged)


def resolve_profile_dir(settings: Settings) -> Path:
    return Path(settings.profile.path)


def provider_profile_path(settings: Settings, name: str) -> Path:
    provider = getattr(settings.providers, name, None)
    if provider is not None and provider.profile:
        return Path(provider.profile)
    if name == "deepseek":
        return Path(settings.profile.path)
    return Path("data/profiles") / name


def enabled_provider_names(settings: Settings) -> list[str]:
    names: list[str] = []
    for name in ("deepseek", "yuanbao"):
        provider = getattr(settings.providers, name, None)
        if provider is not None and provider.enabled:
            names.append(name)
    return names
