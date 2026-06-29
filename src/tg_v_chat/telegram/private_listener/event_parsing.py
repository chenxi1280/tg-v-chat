from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from tg_v_chat.domain import IncomingPrivateMessage, MediaKind

if TYPE_CHECKING:
    from tg_v_chat.telegram.private_listener.process import BoundListenerSession


def private_message_from_event(binding: BoundListenerSession, event) -> IncomingPrivateMessage:
    return _private_message_from_event(
        binding,
        event,
        peer_access_hash=_peer_access_hash_from_attrs(event),
        sender_name=None,
        sent_at=None,
    )


async def async_private_message_from_event(binding: BoundListenerSession, event) -> IncomingPrivateMessage:
    access_hash = await _peer_access_hash(event)
    return _private_message_from_event(
        binding,
        event,
        peer_access_hash=access_hash,
        sender_name=await _sender_name(event),
        sent_at=_sent_at(event),
    )


def _private_message_from_event(
    binding: BoundListenerSession,
    event,
    *,
    peer_access_hash: int | None,
    sender_name: str | None,
    sent_at: datetime | None,
) -> IncomingPrivateMessage:
    message = event.message
    return IncomingPrivateMessage(
        bound_tg_account_id=binding.account_id,
        peer_id=_peer_id(event),
        peer_access_hash=peer_access_hash,
        source_message_id=message.id,
        media_kind=_media_kind(message),
        payload=_payload(event),
        media_group_id=_media_group_id(message),
        sequence=1,
        sender_name=sender_name,
        sent_at=sent_at,
    )


def _peer_id(event) -> int:
    return int(getattr(event, "chat_id", None) or event.sender_id)


async def _peer_access_hash(event) -> int | None:
    access_hash = _peer_access_hash_from_attrs(event)
    if access_hash is not None:
        return access_hash
    for method_name in ("get_input_chat", "get_input_sender", "get_chat", "get_sender"):
        access_hash = await _peer_access_hash_from_method(event, method_name)
        if access_hash is not None:
            return access_hash
    return None


async def _peer_access_hash_from_method(event, method_name: str) -> int | None:
    method = getattr(event, method_name, None)
    if method is None:
        return None
    peer = await method()
    return _access_hash(peer)


def _peer_access_hash_from_attrs(event) -> int | None:
    peer = getattr(event, "input_chat", None) or getattr(event, "input_sender", None)
    return _access_hash(peer)


def _access_hash(peer) -> int | None:
    access_hash = getattr(peer, "access_hash", None)
    return int(access_hash) if access_hash is not None else None


async def _sender_name(event) -> str | None:
    name = _entity_name(getattr(event, "sender", None)) or _entity_name(getattr(event, "chat", None))
    if name:
        return name
    for method_name in ("get_sender", "get_chat"):
        method = getattr(event, method_name, None)
        if method is None:
            continue
        name = _entity_name(await method())
        if name:
            return name
    return None


def _entity_name(entity) -> str | None:
    if entity is None:
        return None
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    full_name = " ".join(part for part in parts if part)
    return full_name or getattr(entity, "title", None) or getattr(entity, "username", None)


def _sent_at(event) -> datetime | None:
    value = getattr(event.message, "date", None)
    return value if isinstance(value, datetime) else None


def _media_kind(message) -> MediaKind:
    if getattr(message, "photo", None) is not None:
        return MediaKind.PHOTO
    if getattr(message, "sticker", None) is not None:
        return MediaKind.STICKER
    return MediaKind.TEXT


def _payload(event) -> str:
    raw_text = getattr(event, "raw_text", None)
    if raw_text:
        return raw_text
    return f"[{_media_kind(event.message).value}]"


def _media_group_id(message) -> str | None:
    grouped_id = getattr(message, "grouped_id", None)
    if grouped_id is None:
        return None
    return str(grouped_id)
