from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tg_v_chat.domain import IncomingPrivateMessage

DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
TELEGRAM_OFFICIAL_PEER_ID = 777000
TELEGRAM_OFFICIAL_NAMES = {"telegram"}


def _format_push_message(message: IncomingPrivateMessage) -> str:
    sender_line = _format_sender_line(message)
    return "\n".join(
        (
            sender_line,
            f"接收账号：{message.recipient_account_name or '未命名账号'}",
            f"接收用户名：{_format_username(message.recipient_username)}",
            f"时间：{_format_message_time(message.sent_at)}",
            f"内容：{message.payload}",
        )
    )


def _format_sender_line(message: IncomingPrivateMessage) -> str:
    sender = message.sender_name or "未知"
    if _is_telegram_official_message(message):
        return f"官方登录信息：{sender}"
    return f"发送人：{sender}"


def _is_telegram_official_message(message: IncomingPrivateMessage) -> bool:
    if message.peer_id == TELEGRAM_OFFICIAL_PEER_ID:
        return True
    sender_name = (message.sender_name or "").strip().casefold()
    return sender_name in TELEGRAM_OFFICIAL_NAMES


def _format_username(value: str | None) -> str:
    if not value:
        return "未设置"
    return value if value.startswith("@") else f"@{value}"


def _format_message_time(value: datetime | None) -> str:
    if value is None:
        return "未知"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
