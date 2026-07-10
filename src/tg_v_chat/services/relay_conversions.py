from __future__ import annotations

from dataclasses import replace

from tg_v_chat.domain import (
    DeveloperSlot,
    IncomingPrivateMessage,
    OutgoingRelayResult,
    SessionSlotRef,
    TelegramPeer,
)


def outgoing_result(row, duplicate: bool) -> OutgoingRelayResult:
    if row.sent_message_id is None or row.developer_slot is None:
        raise RuntimeError("sent outgoing 缺少 Telegram 结果")
    return OutgoingRelayResult(
        relay_message_id=row.relay_message_id,
        sent_message_id=row.sent_message_id,
        developer_slot=DeveloperSlot(row.developer_slot),
        duplicate=duplicate,
    )


def slot_ref(row) -> SessionSlotRef:
    return SessionSlotRef(
        id=row.id,
        bound_tg_account_id=row.bound_tg_account_id,
        developer_slot=DeveloperSlot(row.developer_slot),
        encrypted_session=row.encrypted_session,
    )


def peer_from_mapping(row) -> TelegramPeer:
    return TelegramPeer(id=row.peer_id, access_hash=row.peer_access_hash)


def message_from_relay(row, account) -> IncomingPrivateMessage:
    from tg_v_chat.domain import MediaKind

    return IncomingPrivateMessage(
        bound_tg_account_id=row.bound_tg_account_id,
        peer_id=row.peer_id,
        peer_access_hash=row.peer_access_hash,
        source_message_id=row.source_message_id,
        media_kind=MediaKind(row.media_kind),
        payload=row.payload,
        media_group_id=row.media_group_id,
        sequence=row.sequence,
        sender_name=row.sender_name,
        sent_at=row.sent_at,
        recipient_account_name=account.display_name,
        recipient_username=account.username,
    )


def with_account_identity(message: IncomingPrivateMessage, account) -> IncomingPrivateMessage:
    return replace(
        message,
        recipient_account_name=message.recipient_account_name or account.display_name,
        recipient_username=message.recipient_username or account.username,
    )
