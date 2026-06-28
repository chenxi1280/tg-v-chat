from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tg_v_chat.bot.router import BotIncomingMessage, BotUpdateRouter
from tg_v_chat.domain import IncomingPrivateMessage, OutgoingReply, SessionFailure, SessionSlotRef


@dataclass(frozen=True)
class DeveloperAppConfig:
    api_id: int
    api_hash: str


class TelethonBotGateway:
    def __init__(self, send_message: Callable[[int, IncomingPrivateMessage], int] | None = None):
        self._send_message = send_message

    def push_private_message(self, system_user_id: int, message: IncomingPrivateMessage) -> int:
        if self._send_message is None:
            raise RuntimeError("Telethon bot client is not connected")
        return self._send_message(system_user_id, message)

    def notify_failure(self, system_user_id: int, text: str) -> None:
        if self._send_message is None:
            raise RuntimeError("Telethon bot client is not connected")
        failure_message = IncomingPrivateMessage(0, system_user_id, 0, message_kind_text(), text, None, 0)
        self._send_message(system_user_id, failure_message)


class TelethonSenderPool:
    def __init__(self, send_reply: Callable[[SessionSlotRef, int, OutgoingReply], int] | None = None):
        self._send_reply = send_reply

    def send_reply(self, session_slot: SessionSlotRef, peer_id: int, reply: OutgoingReply) -> int:
        if session_slot.encrypted_session is None:
            raise SessionFailure(f"{session_slot.developer_slot.value} session 未授权")
        if self._send_reply is None:
            raise SessionFailure("Telethon user session client is not connected")
        return self._send_reply(session_slot, peer_id, reply)


def message_kind_text():
    from tg_v_chat.domain import MediaKind

    return MediaKind.TEXT


class TelethonBotProcess:
    def __init__(
        self,
        app_config: DeveloperAppConfig,
        bot_token: str,
        handle_reply: Callable[[object], object],
    ):
        self._app_config = app_config
        self._bot_token = bot_token
        self._handle_reply = handle_reply

    def run(self) -> None:
        import asyncio

        asyncio.run(self._run())

    async def _run(self) -> None:
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), self._app_config.api_id, self._app_config.api_hash)
        await client.start(bot_token=self._bot_token)
        client.add_event_handler(self._handle_new_message, events.NewMessage(incoming=True))
        print("tg-v-chat bot connected")
        await client.run_until_disconnected()

    async def _handle_new_message(self, event) -> None:
        if not getattr(event, "is_private", False):
            return
        replies: list[tuple[int, str]] = []
        router = BotUpdateRouter(self._handle_reply, replies.append)
        router.handle(_incoming_message(event))
        for _message_id, text in replies:
            await event.reply(text)


def _incoming_message(event) -> BotIncomingMessage:
    message = event.message
    return BotIncomingMessage(
        system_user_id=event.sender_id,
        message_id=message.id,
        reply_to_message_id=_reply_to_message_id(message),
        text=event.raw_text or "",
        media_kind=message_kind_text(),
    )


def _reply_to_message_id(message) -> int | None:
    reply = getattr(message, "reply_to", None)
    if reply is None:
        return None
    return getattr(reply, "reply_to_msg_id", None)
