from __future__ import annotations

from tg_v_chat.domain import DeveloperSlot, IncomingPrivateMessage
from tg_v_chat.storage.models import (
    BotPushMessageModel,
    OutgoingReplyModel,
    RelayMessageModel,
    ReplyMappingModel,
)


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


class PushRepository:
    def __init__(self, session):
        self._session = session

    def create(self, relay_id: int, system_user_id: int, bot_message_id: int) -> BotPushMessageModel:
        model = BotPushMessageModel(
            relay_message_id=relay_id,
            system_user_id=system_user_id,
            bot_message_id=bot_message_id,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get_by_relay(self, relay_id: int) -> BotPushMessageModel | None:
        return self._session.query(BotPushMessageModel).filter_by(relay_message_id=relay_id).one_or_none()


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


class OutgoingReplyRepository:
    def __init__(self, session):
        self._session = session

    def create(
        self,
        reply_id: int,
        *,
        system_user_id: int,
        relay_id: int,
        sent_id: int,
        slot: DeveloperSlot,
    ) -> OutgoingReplyModel:
        model = OutgoingReplyModel(
            bot_reply_message_id=reply_id,
            system_user_id=system_user_id,
            relay_message_id=relay_id,
            sent_message_id=sent_id,
            developer_slot=slot.value,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get_by_reply(self, system_user_id: int, reply_id: int) -> OutgoingReplyModel | None:
        return (
            self._session.query(OutgoingReplyModel)
            .filter_by(system_user_id=system_user_id, bot_reply_message_id=reply_id)
            .one_or_none()
        )
