from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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
