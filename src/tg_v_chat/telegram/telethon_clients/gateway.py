from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo

from tg_v_chat.domain import (
    DeliveryFailure,
    DeliveryUncertain,
    IncomingPrivateBatch,
    IncomingPrivateMessage,
    MediaKind,
    OutgoingReply,
    SessionFailure,
    SessionSlotRef,
    TelegramPeer,
)
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.telegram.telethon_clients.config import message_kind_text
from tg_v_chat.telegram.telethon_clients.helpers import _input_peer, _run_async


logger = logging.getLogger(__name__)
DOMAIN_DELIVERY_ERRORS = (DeliveryFailure, DeliveryUncertain, SessionFailure)
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
TELEGRAM_OFFICIAL_PEER_ID = 777000
TELEGRAM_OFFICIAL_NAMES = {"telegram"}


class TelethonBotGateway:
    def __init__(self, send_message: Callable[[int, IncomingPrivateMessage], int] | None = None):
        self._send_message = send_message
        self._client = None
        self._media_store = None
        self._loop = None

    @classmethod
    def from_client(cls, client, media_store: MediaStore, *, loop=None):
        gateway = cls()
        gateway._client = client
        gateway._media_store = media_store
        gateway._loop = loop
        return gateway

    def push_private_message(self, system_user_id: int, message: IncomingPrivateMessage) -> int:
        if self._client is not None:
            return self._run_client_call(self._push_with_client(system_user_id, message))
        if self._send_message is None:
            raise DeliveryFailure("bot_not_connected", "Telethon bot client is not connected")
        try:
            return self._send_message(system_user_id, message)
        except DOMAIN_DELIVERY_ERRORS:
            raise
        except Exception as exc:
            raise _classified_bot_push_error(exc) from exc

    def push_private_batch(self, system_user_id: int, batch: IncomingPrivateBatch) -> list[int]:
        if self._client is None:
            return [self.push_private_message(system_user_id, message) for message in batch.messages]
        return self._run_client_call(self._push_batch_with_client(system_user_id, batch))

    def _run_client_call(self, coroutine):
        if self._loop is None:
            return _run_async(coroutine)
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    async def _push_with_client(self, system_user_id: int, message: IncomingPrivateMessage) -> int:
        try:
            if message.media_kind is MediaKind.TEXT:
                sent = await self._client.send_message(system_user_id, _format_push_message(message))
                return sent.id
            await self._client.send_message(system_user_id, _format_push_message(message))
            sent = await self._client.send_file(
                system_user_id,
                str(_single_artifact_path(self._media_store, message)),
                caption=None,
            )
            return sent.id
        except DOMAIN_DELIVERY_ERRORS:
            raise
        except Exception as exc:
            raise _classified_bot_push_error(exc) from exc

    async def _push_batch_with_client(self, system_user_id: int, batch: IncomingPrivateBatch) -> list[int]:
        try:
            await self._client.send_message(system_user_id, _format_push_message(batch.messages[0]))
            files = [str(_single_artifact_path(self._media_store, message)) for message in batch.messages]
            sent = await self._client.send_file(system_user_id, files, caption=None)
            return [item.id for item in sent]
        except DOMAIN_DELIVERY_ERRORS:
            raise
        except Exception as exc:
            raise _classified_bot_push_error(exc) from exc

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
        try:
            return self._send_reply(session_slot, peer, reply)
        except DOMAIN_DELIVERY_ERRORS:
            raise
        except Exception as exc:
            raise _classified_send_error(exc, session_slot.developer_slot.value) from exc


class TelethonReplySender:
    def __init__(self, app_configs: dict, cipher, *, media_store: MediaStore | None = None):
        self._app_configs = app_configs
        self._cipher = cipher
        self._media_store = media_store

    def send_reply(self, session_slot: SessionSlotRef, peer: TelegramPeer, reply: OutgoingReply) -> int:
        if session_slot.encrypted_session is None:
            raise SessionFailure(f"{session_slot.developer_slot.value} session 未授权")
        try:
            session_string = self._cipher.decrypt(session_slot.encrypted_session)
        except DOMAIN_DELIVERY_ERRORS:
            raise
        except Exception as exc:
            raise SessionFailure(f"{session_slot.developer_slot.value} session 解密失败: {exc}") from exc
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

        try:
            app_config = self._app_configs[session_slot.developer_slot]
            client = TelegramClient(StringSession(session_string), app_config.api_id, app_config.api_hash)
            await client.connect()
        except DOMAIN_DELIVERY_ERRORS:
            raise
        except Exception as exc:
            raise SessionFailure(f"{session_slot.developer_slot.value} 连接失败: {exc}") from exc
        try:
            if reply.media_kind is MediaKind.TEXT:
                sent = await client.send_message(_input_peer(peer), reply.payload)
            else:
                sent = await client.send_file(_input_peer(peer), _reply_file(self._media_store, reply), caption=reply.payload)
            return _sent_message_id(sent)
        except DOMAIN_DELIVERY_ERRORS:
            raise
        except Exception as exc:
            raise _classified_send_error(exc, session_slot.developer_slot.value) from exc
        finally:
            await _disconnect(client, session_slot.developer_slot.value)


async def _disconnect(client, slot_name: str) -> None:
    try:
        await client.disconnect()
    except Exception:
        logger.exception("%s disconnect failed after send outcome was determined", slot_name)


def _classified_bot_push_error(error: Exception) -> Exception:
    classified = _classified_send_error(error, "bot")
    if isinstance(classified, SessionFailure):
        return DeliveryFailure("bot_session_failure", str(classified))
    if isinstance(classified, DeliveryFailure) and classified.code == "send_unknown":
        return DeliveryFailure("bot_push_failed", classified.detail)
    return classified


def _classified_send_error(error: Exception, slot_name: str) -> Exception:
    from telethon.errors import (
        AuthKeyError,
        ChatWriteForbiddenError,
        FloodWaitError,
        PeerIdInvalidError,
        RPCError,
        UnauthorizedError,
    )

    if isinstance(error, (SessionFailure, DeliveryFailure, DeliveryUncertain)):
        return error
    if isinstance(error, (UnauthorizedError, AuthKeyError)):
        return SessionFailure(f"{slot_name} session 不可用: {error}")
    if isinstance(error, (ConnectionError, OSError, TimeoutError)):
        return DeliveryUncertain("transport_unknown", f"{slot_name} 发送结果未知: {error}")
    if isinstance(error, PeerIdInvalidError):
        return DeliveryFailure("peer_invalid", str(error))
    if isinstance(error, ChatWriteForbiddenError):
        return DeliveryFailure("permission_denied", str(error))
    if isinstance(error, FloodWaitError):
        return DeliveryFailure("flood_wait", str(error))
    if isinstance(error, RPCError):
        return DeliveryFailure("telegram_rpc", str(error))
    return DeliveryFailure("send_unknown", f"{slot_name} 发送失败: {error}")


def _single_artifact_path(media_store, message: IncomingPrivateMessage):
    if media_store is None:
        raise DeliveryFailure("media_store_missing", "media store is required for media push")
    if len(message.artifacts) != 1:
        raise DeliveryFailure("media_artifact_missing", "media message must have exactly one artifact")
    return media_store.path_for(message.artifacts[0])


def _reply_file(media_store, reply: OutgoingReply):
    if media_store is None:
        raise DeliveryFailure("media_store_missing", "media store is required for media reply")
    if not reply.artifacts:
        raise DeliveryFailure("media_artifact_missing", "media reply must include artifacts")
    files = [str(media_store.path_for(artifact)) for artifact in reply.artifacts]
    return files[0] if len(files) == 1 else files


def _sent_message_id(sent) -> int:
    if isinstance(sent, list):
        if not sent:
            raise DeliveryFailure("send_empty_result", "Telegram media send returned no messages")
        return sent[0].id
    return sent.id


def _format_push_message(message: IncomingPrivateMessage) -> str:
    return "\n".join(
        (
            _format_sender_line(message),
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
    sender_name = (message.sender_name or "").strip().casefold()
    return message.peer_id == TELEGRAM_OFFICIAL_PEER_ID or sender_name in TELEGRAM_OFFICIAL_NAMES


def _format_username(value: str | None) -> str:
    if not value:
        return "未设置"
    return value if value.startswith("@") else f"@{value}"


def _format_message_time(value: datetime | None) -> str:
    if value is None:
        return "未知"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
