from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

from tg_v_chat.domain import IncomingPrivateBatch, IncomingPrivateMessage, MediaKind

if TYPE_CHECKING:
    from tg_v_chat.telegram.private_listener.process import BoundListenerSession


def private_message_from_event(binding: BoundListenerSession, event) -> IncomingPrivateMessage:
    if _media_kind(event.message) is not MediaKind.TEXT or _has_unsupported_media(event.message):
        raise RuntimeError("async media parsing with media_store is required")
    return _private_message_from_event(
        binding,
        event,
        peer_access_hash=_peer_access_hash_from_attrs(event),
        sender_name=None,
        sent_at=None,
    )


async def async_private_message_from_event(
    binding: BoundListenerSession,
    event,
    *,
    media_store=None,
    sequence: int = 1,
) -> IncomingPrivateMessage | None:
    if _media_group_id(event.message) is not None:
        return None
    access_hash = await _peer_access_hash(event)
    artifacts = await _media_artifacts(event.message, media_store, sequence)
    return _private_message_from_event(
        binding,
        event,
        peer_access_hash=access_hash,
        sender_name=await _sender_name(event),
        sent_at=_sent_at(event),
        sequence=sequence,
        artifacts=artifacts,
    )


async def async_private_batch_from_album(binding: BoundListenerSession, event, *, media_store) -> IncomingPrivateBatch:
    if not getattr(event, "is_private", False):
        raise RuntimeError("album event must be private")
    messages = sorted(event.messages, key=lambda item: item.id)
    parsed = []
    try:
        for index, message in enumerate(messages, start=1):
            parsed.append(
                await _album_item_from_message(binding, event, message, media_store=media_store, sequence=index)
            )
    except Exception:
        _discard_batch_artifacts(media_store, parsed)
        raise
    return IncomingPrivateBatch(tuple(parsed))


def _private_message_from_event(
    binding: BoundListenerSession,
    event,
    *,
    peer_access_hash: int | None,
    sender_name: str | None,
    sent_at: datetime | None,
    sequence: int = 1,
    artifacts=(),
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
        sequence=sequence,
        sender_name=sender_name,
        sent_at=sent_at,
        recipient_account_name=binding.display_name,
        recipient_username=binding.username,
        artifacts=tuple(artifacts),
    )


async def _album_item_from_message(binding, event, message, *, media_store, sequence: int):
    item_event = SimpleNamespace(
        chat_id=getattr(event, "chat_id", None) or getattr(event, "sender_id", None),
        sender_id=getattr(event, "sender_id", None),
        raw_text=getattr(message, "message", None) or getattr(event, "raw_text", None) or "",
        input_chat=getattr(event, "input_chat", None),
        input_sender=getattr(event, "input_sender", None),
        message=message,
        sender=getattr(event, "sender", None),
        chat=getattr(event, "chat", None),
    )
    artifacts = await _media_artifacts(message, media_store, sequence)
    return _private_message_from_event(
        binding,
        item_event,
        peer_access_hash=_peer_access_hash_from_attrs(event),
        sender_name=_entity_name(getattr(event, "sender", None)),
        sent_at=_sent_at(item_event),
        sequence=sequence,
        artifacts=artifacts,
    )


async def _media_artifacts(message, media_store, sequence: int):
    media_kind = _media_kind(message)
    if media_kind is MediaKind.TEXT:
        if _has_unsupported_media(message):
            raise RuntimeError("unsupported incoming media")
        return ()
    if media_store is None:
        raise RuntimeError("media_store is required for Telegram media")
    artifact = await media_store.download_message_media(
        message,
        file_name=_media_file_name(message, media_kind),
        mime_type=_mime_type(message, media_kind),
        media_kind=media_kind,
        sequence=sequence,
    )
    return (artifact,)


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
    return ""


def _media_group_id(message) -> str | None:
    grouped_id = getattr(message, "grouped_id", None)
    if grouped_id is None:
        return None
    return str(grouped_id)


def _has_unsupported_media(message) -> bool:
    return bool(getattr(message, "media", None)) and not getattr(message, "photo", None) and not getattr(message, "sticker", None)


def _media_file_name(message, media_kind: MediaKind) -> str:
    file_meta = getattr(message, "file", None)
    name = getattr(file_meta, "name", None)
    if name:
        return name
    return f"{message.id}.{_default_extension(media_kind)}"


def _mime_type(message, media_kind: MediaKind) -> str:
    file_meta = getattr(message, "file", None)
    mime = getattr(file_meta, "mime_type", None)
    if mime:
        return mime
    return "image/webp" if media_kind is MediaKind.STICKER else "image/jpeg"


def _default_extension(media_kind: MediaKind) -> str:
    return "webp" if media_kind is MediaKind.STICKER else "jpg"


def _discard_batch_artifacts(media_store, messages: list[IncomingPrivateMessage]) -> None:
    for message in messages:
        for artifact in message.artifacts:
            media_store.discard(artifact)
