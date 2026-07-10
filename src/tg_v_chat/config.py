from __future__ import annotations

import os
from dataclasses import dataclass


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


def load_config() -> AppConfig:
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
    )


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    if value.startswith("replace_with_"):
        raise RuntimeError(f"environment variable still uses template placeholder: {name}")
    return value
