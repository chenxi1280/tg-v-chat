from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeveloperAppConfig:
    api_id: int
    api_hash: str


def message_kind_text():
    from tg_v_chat.domain import MediaKind

    return MediaKind.TEXT
