from __future__ import annotations

from tg_v_chat.domain import IncomingPrivateMessage
from tg_v_chat.storage.models import (
    RelayMessageModel,
    ReplyMappingModel,
    utc_now,
)
from tg_v_chat.storage.repositories.dispatch import OutgoingReplyRepository, PushRepository


def _relay_values(message: IncomingPrivateMessage) -> dict:
    return {
        "bound_tg_account_id": message.bound_tg_account_id,
        "peer_id": message.peer_id,
        "peer_access_hash": message.peer_access_hash,
        "sender_name": message.sender_name,
        "sent_at": message.sent_at,
        "source_message_id": message.source_message_id,
        "media_kind": message.media_kind.value,
        "payload": message.payload,
        "media_group_id": message.media_group_id,
        "sequence": message.sequence,
    }


class RelayRepository:
    def __init__(self, session):
        self._session = session

    def create_or_get(self, message: IncomingPrivateMessage) -> tuple[RelayMessageModel, bool]:
        existing = self._session.query(RelayMessageModel).filter_by(
            bound_tg_account_id=message.bound_tg_account_id,
            peer_id=message.peer_id,
            source_message_id=message.source_message_id,
        ).one_or_none()
        if existing:
            return existing, True
        model = RelayMessageModel(**_relay_values(message))
        self._session.add(model)
        self._session.flush()
        return model, False

    def list_media_group(self, account_id: int, media_group_id: str) -> list[RelayMessageModel]:
        rows = self._session.query(RelayMessageModel).filter_by(
            bound_tg_account_id=account_id,
            media_group_id=media_group_id,
        ).all()
        return sorted(rows, key=lambda row: row.sequence)

    def has_media_sequence(self, account_id: int, media_group_id: str, sequence: int) -> bool:
        row = self._session.query(RelayMessageModel).filter_by(
            bound_tg_account_id=account_id,
            media_group_id=media_group_id,
            sequence=sequence,
        ).one_or_none()
        return row is not None


class MappingRepository:
    def __init__(self, session):
        self._session = session

    def create(self, bot_message_id: int, relay: RelayMessageModel, system_user_id: int) -> ReplyMappingModel:
        model = ReplyMappingModel(
            bot_message_id=bot_message_id,
            system_user_id=system_user_id,
            relay_message_id=relay.id,
            bound_tg_account_id=relay.bound_tg_account_id,
            peer_id=relay.peer_id,
            peer_access_hash=relay.peer_access_hash,
            source_message_id=relay.source_message_id,
            media_kind=relay.media_kind,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get_by_bot_message(self, system_user_id: int, bot_message_id: int) -> ReplyMappingModel | None:
        return (
            self._session.query(ReplyMappingModel)
            .filter_by(system_user_id=system_user_id, bot_message_id=bot_message_id)
            .one_or_none()
        )

    def invalidate_for_account(self, account_id: int) -> int:
        invalidated_at = utc_now()
        active_mappings = (
            self._session.query(ReplyMappingModel)
            .filter_by(bound_tg_account_id=account_id, status="active")
            .all()
        )
        for mapping in active_mappings:
            mapping.status = "expired"
            mapping.invalidated_at = invalidated_at
        self._session.flush()
        return len(active_mappings)
