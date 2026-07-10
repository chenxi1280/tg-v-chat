from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from tg_v_chat.bot.handlers import BotReplyCommand
from tg_v_chat.domain import MediaArtifact, MediaKind

REPLY_REQUIRED = "请使用 Telegram 的回复功能，回复 Bot 推送的原消息后再发送。"


@dataclass(frozen=True)
class BotIncomingMessage:
    system_user_id: int
    message_id: int
    reply_to_message_id: int | None
    text: str
    media_kind: MediaKind = MediaKind.TEXT
    artifacts: tuple[MediaArtifact, ...] = ()


@dataclass(frozen=True)
class BotCallback:
    system_user_id: int
    data: str


@dataclass(frozen=True)
class ButtonSpec:
    text: str
    data: str


@dataclass(frozen=True)
class BotResponse:
    text: str
    reply_to_message_id: int | None = None
    buttons: tuple[ButtonSpec, ...] = ()
    edit_message: bool = False


class AccountManagement(Protocol):
    def handle_command(self, telegram_user_id: int, command: str) -> BotResponse:
        raise NotImplementedError

    def handle_callback(self, telegram_user_id: int, data: str) -> BotResponse:
        raise NotImplementedError

    def handle_text(self, telegram_user_id: int, text: str) -> BotResponse:
        raise NotImplementedError


class BotUpdateRouter:
    def __init__(
        self,
        handle_reply: Callable[[BotReplyCommand], object],
        account_management: AccountManagement,
    ):
        self._handle_reply = handle_reply
        self._account_management = account_management

    def handle(self, message: BotIncomingMessage) -> list[BotResponse]:
        command = _command(message.text)
        if command:
            return [self._command_response(message, command)]
        if message.reply_to_message_id is None:
            return [self._text_response(message)]
        return self._handle_bot_reply(message)

    def handle_callback(self, callback: BotCallback) -> list[BotResponse]:
        response = self._account_management.handle_callback(callback.system_user_id, callback.data)
        return [BotResponse(response.text, response.reply_to_message_id, response.buttons, edit_message=True)]

    def _command_response(self, message: BotIncomingMessage, command: str) -> BotResponse:
        response = self._account_management.handle_command(message.system_user_id, command)
        return _with_reply_to(response, message.message_id)

    def _text_response(self, message: BotIncomingMessage) -> BotResponse:
        response = self._account_management.handle_text(message.system_user_id, message.text)
        return _with_reply_to(response, message.message_id)

    def _handle_bot_reply(self, message: BotIncomingMessage) -> list[BotResponse]:
        try:
            self._handle_reply(_reply_command(message))
            return []
        except Exception as exc:
            return [BotResponse(f"处理失败：{exc}", reply_to_message_id=message.message_id)]


def _command(text: str) -> str | None:
    normalized = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    return normalized if normalized in {"/start", "/admin", "/accounts", "/bind"} else None


def _with_reply_to(response: BotResponse, message_id: int) -> BotResponse:
    return BotResponse(response.text, message_id, response.buttons, response.edit_message)


def _reply_command(message: BotIncomingMessage) -> BotReplyCommand:
    return BotReplyCommand(
        system_user_id=message.system_user_id,
        bot_reply_message_id=message.message_id,
        reply_to_message_id=message.reply_to_message_id,
        media_kind=message.media_kind,
        payload=message.text,
        artifacts=message.artifacts,
    )
