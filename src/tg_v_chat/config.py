from __future__ import annotations

import os
import re
from dataclasses import dataclass


DEFAULT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS = 30
BOT_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    session_encryption_key: str
    bot_token: str
    primary_api_id: str
    primary_api_hash: str
    standby_1_api_id: str
    standby_1_api_hash: str
    standby_2_api_id: str
    standby_2_api_hash: str
    media_root: str
    heartbeat_root: str
    native_forward_v2_enabled: bool = False
    bot_username: str | None = None
    native_forward_bridge_timeout_seconds: int = DEFAULT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS


def load_config() -> AppConfig:
    native_forward_v2_enabled = _optional_bool_env("TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED", default=False)
    bot_username = _optional_bot_username_env()
    if native_forward_v2_enabled and bot_username is None:
        raise RuntimeError("missing required environment variable: TG_V_CHAT_BOT_USERNAME")
    return AppConfig(
        database_url=_require_env("TG_V_CHAT_DATABASE_URL"),
        session_encryption_key=_require_env("TG_V_CHAT_SESSION_KEY"),
        bot_token=_require_env("TG_V_CHAT_BOT_TOKEN"),
        primary_api_id=_require_env("TG_V_CHAT_PRIMARY_API_ID"),
        primary_api_hash=_require_env("TG_V_CHAT_PRIMARY_API_HASH"),
        standby_1_api_id=_require_env("TG_V_CHAT_STANDBY_1_API_ID"),
        standby_1_api_hash=_require_env("TG_V_CHAT_STANDBY_1_API_HASH"),
        standby_2_api_id=_require_env("TG_V_CHAT_STANDBY_2_API_ID"),
        standby_2_api_hash=_require_env("TG_V_CHAT_STANDBY_2_API_HASH"),
        media_root=_require_env("TG_V_CHAT_MEDIA_ROOT"),
        heartbeat_root=_require_env("TG_V_CHAT_HEARTBEAT_ROOT"),
        native_forward_v2_enabled=native_forward_v2_enabled,
        bot_username=bot_username,
        native_forward_bridge_timeout_seconds=_optional_positive_int_env(
            "TG_V_CHAT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS",
            default=DEFAULT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS,
        ),
    )


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    if value.startswith("replace_with_"):
        raise RuntimeError(f"environment variable still uses template placeholder: {name}")
    return value


def _optional_bool_env(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise RuntimeError(f"environment variable must be true or false: {name}")


def _optional_bot_username_env() -> str | None:
    value = os.getenv("TG_V_CHAT_BOT_USERNAME")
    if value is None or not value.strip():
        return None
    username = value.strip()
    if not BOT_USERNAME_PATTERN.fullmatch(username):
        raise RuntimeError("TG_V_CHAT_BOT_USERNAME must be a Telegram username without @")
    return username


def _optional_positive_int_env(name: str, *, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"environment variable must be an integer: {name}") from exc
    if parsed <= 0:
        raise RuntimeError(f"environment variable must be positive: {name}")
    return parsed
