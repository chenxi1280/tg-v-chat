from __future__ import annotations

from collections.abc import Callable

from tg_v_chat.domain import IncomingPrivateMessage, OutgoingReply, SessionFailure, SessionSlotRef, TelegramPeer
from tg_v_chat.telegram.telethon_clients.config import message_kind_text
from tg_v_chat.telegram.telethon_clients.helpers import _input_peer, _run_async


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
        failure_message = IncomingPrivateMessage(
            bound_tg_account_id=0,
            peer_id=system_user_id,
            source_message_id=0,
            media_kind=message_kind_text(),
            payload=text,
            media_group_id=None,
            sequence=0,
        )
        self._send_message(system_user_id, failure_message)


class TelethonSenderPool:
    def __init__(self, send_reply: Callable[[SessionSlotRef, TelegramPeer, OutgoingReply], int] | None = None):
        self._send_reply = send_reply

    def send_reply(self, session_slot: SessionSlotRef, peer: TelegramPeer, reply: OutgoingReply) -> int:
        if session_slot.encrypted_session is None:
            raise SessionFailure(f"{session_slot.developer_slot.value} session 未授权")
        if self._send_reply is None:
            raise SessionFailure("Telethon user session client is not connected")
        return self._send_reply(session_slot, peer, reply)


class TelethonReplySender:
    def __init__(self, app_configs: dict, cipher):
        self._app_configs = app_configs
        self._cipher = cipher

    def send_reply(self, session_slot: SessionSlotRef, peer: TelegramPeer, reply: OutgoingReply) -> int:
        if session_slot.encrypted_session is None:
            raise SessionFailure(f"{session_slot.developer_slot.value} session 未授权")
        session_string = self._cipher.decrypt(session_slot.encrypted_session)
        return _run_async(self._send(session_slot, session_string=session_string, peer=peer, reply=reply))

    async def _send(
        self,
        session_slot: SessionSlotRef,
        *,
        session_string: str,
        peer: TelegramPeer,
        reply: OutgoingReply,
    ) -> int:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        app_config = self._app_configs[session_slot.developer_slot]
        client = TelegramClient(StringSession(session_string), app_config.api_id, app_config.api_hash)
        await client.connect()
        try:
            sent = await client.send_message(_input_peer(peer), reply.payload)
            return sent.id
        except Exception as exc:
            raise SessionFailure(f"{session_slot.developer_slot.value} 发送失败: {exc}") from exc
        finally:
            await client.disconnect()
