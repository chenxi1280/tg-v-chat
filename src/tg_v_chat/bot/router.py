from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tg_v_chat.bot.handlers import BotReplyCommand
from tg_v_chat.domain import MediaKind

START_REPLY = "机器人在线。请直接回复 Bot 推送的私聊消息进行代发。"
ADMIN_REPLY = "机器人在线。当前未启用独立管理面板；绑定与中转失败会直接返回明确错误。"
REPLY_REQUIRED = "请使用 Telegram 的回复功能，回复 Bot 推送的原消息后再发送。"


@dataclass(frozen=True)
class BotIncomingMessage:
    system_user_id: int
    message_id: int
    reply_to_message_id: int | None
    text: str
    media_kind: MediaKind = MediaKind.TEXT


class BotUpdateRouter:
    def __init__(
        self,
        handle_reply: Callable[[BotReplyCommand], object],
        reply: Callable[[tuple[int, str]], None],
    ):
        self._handle_reply = handle_reply
        self._reply = reply

    def handle(self, message: BotIncomingMessage) -> None:
        command_reply = _command_reply(message.text)
        if command_reply:
            self._reply((message.message_id, command_reply))
            return
        if message.reply_to_message_id is None:
            self._reply((message.message_id, REPLY_REQUIRED))
            return
        self._handle_bot_reply(message)

    def _handle_bot_reply(self, message: BotIncomingMessage) -> None:
        try:
            self._handle_reply(_reply_command(message))
        except Exception as exc:
            self._reply((message.message_id, f"处理失败：{exc}"))


def _command_reply(text: str) -> str | None:
    normalized = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    if normalized == "/start":
        return START_REPLY
    if normalized == "/admin":
        return ADMIN_REPLY
    return None


def _reply_command(message: BotIncomingMessage) -> BotReplyCommand:
    return BotReplyCommand(
        system_user_id=message.system_user_id,
        bot_reply_message_id=message.message_id,
        reply_to_message_id=message.reply_to_message_id,
        media_kind=message.media_kind,
        payload=message.text,
    )
