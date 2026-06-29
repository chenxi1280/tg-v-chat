from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tg_v_chat.domain import IncomingPrivateMessage

DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _format_push_message(message: IncomingPrivateMessage) -> str:
    return "\n".join(
        (
            f"发送人：{message.sender_name or '未知'}",
            f"时间：{_format_message_time(message.sent_at)}",
            f"内容：{message.payload}",
        )
    )


def _format_message_time(value: datetime | None) -> str:
    if value is None:
        return "未知"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
