from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func

from tg_v_chat.domain import TelegramPeer
from tg_v_chat.storage.models import (
    BotPushMessageModel,
    BoundTgAccountModel,
    NativeForwardBatchModel,
    NativeForwardBridgeQuarantineModel,
    NativeForwardItemModel,
    RelayMessageModel,
    ReplyMappingModel,
)
from tg_v_chat.storage.repositories.dispatch import PushRepository


BRIDGE_ACTIVE_STATUSES = ("bridge_sending", "awaiting_bot", "final_sending")
INTERRUPTIBLE_BATCH_STATUSES = ("collecting", "sealed", "bridge_sending", "awaiting_bot", "final_sending")
ITEM_TERMINAL_STATUSES = ("sent", "failed", "uncertain")
PUSH_ACTIVE_STATUSES = ("pending", "sending")
OPERATIONAL_ACCOUNT_STATUSES = ("active", "degraded")


@dataclass(frozen=True)
class BridgeItemAppend:
    batch: NativeForwardBatchModel
    item: NativeForwardItemModel
    duplicate: bool
    complete: bool


class NativeForwardRepository:
    def __init__(self, session):
        self._session = session

    def create_collecting(
        self,
        *,
        system_user_id: int,
        account_id: int,
        telegram_user_id: int,
        peer: TelegramPeer,
        collect_until: datetime,
        marker_token: str,
    ) -> NativeForwardBatchModel:
        model = NativeForwardBatchModel(
            system_user_id=system_user_id,
            bound_tg_account_id=account_id,
            bridge_sender_telegram_user_id=telegram_user_id,
            source_peer_id=peer.id,
            source_peer_access_hash=peer.access_hash,
            collect_until=collect_until,
            marker_token=marker_token,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def find_collecting(self, account_id: int, peer_id: int, now: datetime) -> NativeForwardBatchModel | None:
        return (
            self._session.query(NativeForwardBatchModel)
            .filter_by(bound_tg_account_id=account_id, source_peer_id=peer_id, status="collecting")
            .filter(NativeForwardBatchModel.collect_until >= now)
            .filter(NativeForwardBatchModel.expected_count < 100)
            .order_by(NativeForwardBatchModel.created_at.desc(), NativeForwardBatchModel.id.desc())
            .first()
        )

    def append_item(self, batch_id: int, relay_message_id: int) -> NativeForwardItemModel:
        existing = self.get_item_by_relay(relay_message_id)
        if existing is not None:
            return existing
        batch = self._batch_for_update(batch_id)
        if batch.status != "collecting":
            raise ValueError(f"native forward batch is not collecting: {batch.status}")
        sequence = self._next_batch_sequence(batch.id)
        if sequence > 100:
            raise ValueError("native forward batch cannot contain more than 100 items")
        item = NativeForwardItemModel(
            batch_id=batch.id,
            relay_message_id=relay_message_id,
            batch_sequence=sequence,
            bridge_sender_telegram_user_id=batch.bridge_sender_telegram_user_id,
        )
        batch.expected_count = sequence
        self._session.add(item)
        self._session.flush()
        return item

    def seal(self, batch_id: int) -> NativeForwardBatchModel:
        batch = self.get(batch_id)
        if batch.status != "collecting":
            return batch
        if batch.expected_count == 0:
            raise ValueError("native forward batch cannot seal without items")
        batch.status = "sealed"
        self._session.flush()
        return batch

    def seal_due(self, now: datetime, *, account_id: int | None = None) -> list[NativeForwardBatchModel]:
        query = self._session.query(NativeForwardBatchModel).filter_by(status="collecting")
        query = query.filter(NativeForwardBatchModel.collect_until <= now)
        if account_id is not None:
            query = query.filter_by(bound_tg_account_id=account_id)
        batches = query.order_by(NativeForwardBatchModel.id.asc()).all()
        return [self.seal(batch.id) for batch in batches if batch.expected_count > 0]

    def list_sealed_for_account(self, account_id: int) -> list[NativeForwardBatchModel]:
        return (
            self._session.query(NativeForwardBatchModel)
            .filter_by(bound_tg_account_id=account_id, status="sealed")
            .order_by(NativeForwardBatchModel.id.asc())
            .all()
        )

    def claim_bridge(self, batch_id: int, bridge_deadline_at: datetime) -> NativeForwardBatchModel | None:
        batch = self._batch_for_update(batch_id)
        if batch.status != "sealed":
            return None
        if self._has_other_active_bridge(batch):
            return None
        batch.status = "bridge_sending"
        batch.bridge_deadline_at = bridge_deadline_at
        self._session.flush()
        return batch

    def mark_awaiting_bot(
        self,
        marker_token: str,
        sender_telegram_user_id: int,
        *,
        marker_message_id: int,
    ) -> NativeForwardBatchModel | None:
        batch = self.get_by_marker(marker_token)
        if batch is None or batch.bridge_sender_telegram_user_id != sender_telegram_user_id:
            return None
        if batch.status == "awaiting_bot":
            return batch if batch.first_hop_marker_message_id == marker_message_id else None
        if batch.status != "bridge_sending":
            return None
        batch.status = "awaiting_bot"
        batch.first_hop_marker_message_id = marker_message_id
        self._session.flush()
        return batch

    def find_active_bridge(self, sender_telegram_user_id: int) -> NativeForwardBatchModel | None:
        return (
            self._session.query(NativeForwardBatchModel)
            .filter_by(bridge_sender_telegram_user_id=sender_telegram_user_id, status="awaiting_bot")
            .order_by(NativeForwardBatchModel.created_at.asc(), NativeForwardBatchModel.id.asc())
            .first()
        )

    def append_bridge_message(
        self,
        sender_telegram_user_id: int,
        bridge_message_id: int,
        identity_visibility: str,
    ) -> BridgeItemAppend:
        batch = self.find_active_bridge(sender_telegram_user_id)
        if batch is None:
            raise LookupError("native forward bridge is not awaiting items")
        item = self._bridge_item_by_message(sender_telegram_user_id, bridge_message_id)
        if item is not None:
            if item.batch_id != batch.id:
                raise ValueError("native forward bridge message belongs to another batch")
            return BridgeItemAppend(batch, item, True, self._bridge_complete(batch))
        item = self._next_unbridged_item(batch.id)
        if item is None:
            raise ValueError("native forward bridge has no unbridged item")
        item.bridge_message_id = bridge_message_id
        item.identity_visibility = identity_visibility
        item.status = "bridged"
        self._session.flush()
        complete = self._bridge_complete(batch)
        if complete:
            self._order_bridged_items(batch.id)
        return BridgeItemAppend(batch, item, False, complete)

    def claim_final(self, marker_token: str) -> NativeForwardBatchModel | None:
        batch = self.get_by_marker(marker_token)
        if batch is None or batch.status != "awaiting_bot" or not self._bridge_complete(batch):
            return None
        account = self._session.get(BoundTgAccountModel, batch.bound_tg_account_id)
        if account is None or account.status not in OPERATIONAL_ACCOUNT_STATUSES:
            self._mark_terminal(batch.id, "failed", "account_unavailable", "账号已不可用，取消原生转发")
            return None
        batch.status = "final_sending"
        self._session.flush()
        return batch

    def create_and_claim_final_pushes(self, batch_id: int) -> list[BotPushMessageModel]:
        batch = self.get(batch_id)
        if batch.status != "final_sending":
            raise ValueError(f"native forward batch cannot claim final pushes: {batch.status}")
        pushes = []
        push_repository = PushRepository(self._session)
        for item in self.list_items(batch.id):
            push = self._claim_item_push(push_repository, batch, item)
            pushes.append(push)
        self._session.flush()
        return pushes

    def record_header(self, marker_token: str, header_bot_message_id: int) -> NativeForwardBatchModel:
        batch = self._require_final_sending(marker_token)
        batch.header_bot_message_id = header_bot_message_id
        self._session.flush()
        return batch

    def mark_final_sent(self, marker_token: str, final_message_ids: tuple[int, ...]) -> NativeForwardBatchModel:
        batch = self._require_final_sending(marker_token)
        items = self.list_items(batch.id)
        if len(final_message_ids) != len(items):
            raise ValueError("native forward final message count mismatch")
        for item, final_message_id in zip(items, final_message_ids):
            self._finish_item(batch, item, final_message_id)
        batch.status = "sent"
        self._session.flush()
        return batch

    def mark_failed(self, batch_id: int, code: str, detail: str) -> NativeForwardBatchModel:
        return self._mark_terminal(batch_id, "failed", code, detail)

    def mark_uncertain(self, batch_id: int, code: str, detail: str) -> NativeForwardBatchModel:
        return self._mark_terminal(batch_id, "uncertain", code, detail)

    def expire_bridge_batches(self, now: datetime) -> list[NativeForwardBatchModel]:
        batches = (
            self._session.query(NativeForwardBatchModel)
            .filter(NativeForwardBatchModel.status.in_(("bridge_sending", "awaiting_bot", "final_sending")))
            .filter(NativeForwardBatchModel.bridge_deadline_at <= now)
            .order_by(NativeForwardBatchModel.id.asc())
            .all()
        )
        return [self.mark_uncertain(batch.id, "bridge_timeout", "原生转发桥接超时") for batch in batches]

    def cancel_active_for_account(self, account_id: int, code: str, detail: str) -> list[NativeForwardBatchModel]:
        batches = (
            self._session.query(NativeForwardBatchModel)
            .filter_by(bound_tg_account_id=account_id)
            .filter(NativeForwardBatchModel.status.in_(INTERRUPTIBLE_BATCH_STATUSES))
            .order_by(NativeForwardBatchModel.id.asc())
            .all()
        )
        return [self.mark_failed(batch.id, code, detail) for batch in batches]

    def record_quarantine(
        self,
        sender_telegram_user_id: int,
        bot_message_id: int,
        marker_token: str | None,
        failure_code: str,
    ) -> NativeForwardBridgeQuarantineModel:
        quarantine = NativeForwardBridgeQuarantineModel(
            sender_telegram_user_id=sender_telegram_user_id,
            bot_message_id=bot_message_id,
            marker_token=marker_token,
            failure_code=failure_code,
        )
        self._session.add(quarantine)
        self._session.flush()
        return quarantine

    def get(self, batch_id: int) -> NativeForwardBatchModel:
        batch = self._session.get(NativeForwardBatchModel, batch_id)
        if batch is None:
            raise LookupError(f"NativeForwardBatch 不存在: {batch_id}")
        return batch

    def get_by_marker(self, marker_token: str) -> NativeForwardBatchModel | None:
        return self._session.query(NativeForwardBatchModel).filter_by(marker_token=marker_token).one_or_none()

    def get_item_by_relay(self, relay_message_id: int) -> NativeForwardItemModel | None:
        return self._session.query(NativeForwardItemModel).filter_by(relay_message_id=relay_message_id).one_or_none()

    def list_items(self, batch_id: int) -> list[NativeForwardItemModel]:
        return (
            self._session.query(NativeForwardItemModel)
            .filter_by(batch_id=batch_id)
            .order_by(NativeForwardItemModel.batch_sequence.asc())
            .all()
        )

    def list_pushes_for_batch(self, batch_id: int) -> list[BotPushMessageModel]:
        item_ids = [item.bot_push_message_id for item in self.list_items(batch_id) if item.bot_push_message_id]
        if not item_ids:
            return []
        return self._session.query(BotPushMessageModel).filter(BotPushMessageModel.id.in_(item_ids)).order_by(BotPushMessageModel.id.asc()).all()

    def _batch_for_update(self, batch_id: int) -> NativeForwardBatchModel:
        batch = self._session.query(NativeForwardBatchModel).filter_by(id=batch_id).with_for_update().one_or_none()
        if batch is None:
            raise LookupError(f"NativeForwardBatch 不存在: {batch_id}")
        return batch

    def _next_batch_sequence(self, batch_id: int) -> int:
        current = self._session.query(func.max(NativeForwardItemModel.batch_sequence)).filter_by(batch_id=batch_id).scalar()
        return int(current or 0) + 1

    def _has_other_active_bridge(self, batch: NativeForwardBatchModel) -> bool:
        return (
            self._session.query(NativeForwardBatchModel.id)
            .filter(NativeForwardBatchModel.bridge_sender_telegram_user_id == batch.bridge_sender_telegram_user_id)
            .filter(NativeForwardBatchModel.status.in_(BRIDGE_ACTIVE_STATUSES))
            .filter(NativeForwardBatchModel.id != batch.id)
            .first()
            is not None
        )

    def _bridge_item_by_message(self, sender_telegram_user_id: int, bridge_message_id: int) -> NativeForwardItemModel | None:
        return (
            self._session.query(NativeForwardItemModel)
            .filter_by(
                bridge_sender_telegram_user_id=sender_telegram_user_id,
                bridge_message_id=bridge_message_id,
            )
            .one_or_none()
        )

    def _next_unbridged_item(self, batch_id: int) -> NativeForwardItemModel | None:
        return (
            self._session.query(NativeForwardItemModel)
            .filter_by(batch_id=batch_id, bridge_message_id=None)
            .order_by(NativeForwardItemModel.batch_sequence.asc())
            .first()
        )

    def _order_bridged_items(self, batch_id: int) -> None:
        items = self.list_items(batch_id)
        received = sorted((item.bridge_message_id, item.identity_visibility) for item in items)
        for item in items:
            item.bridge_message_id = None
        self._session.flush()
        for item, (bridge_message_id, identity_visibility) in zip(items, received):
            item.bridge_message_id = bridge_message_id
            item.identity_visibility = identity_visibility
        self._session.flush()

    def _bridge_complete(self, batch: NativeForwardBatchModel) -> bool:
        bridged_count = (
            self._session.query(NativeForwardItemModel)
            .filter_by(batch_id=batch.id)
            .filter(NativeForwardItemModel.bridge_message_id.is_not(None))
            .count()
        )
        return batch.expected_count > 0 and bridged_count == batch.expected_count

    def _claim_item_push(self, pushes: PushRepository, batch, item) -> BotPushMessageModel:
        push = pushes.get_by_relay(item.relay_message_id)
        if push is None:
            push = pushes.create_pending(item.relay_message_id, batch.system_user_id, f"push:{item.relay_message_id}")
        if not pushes.claim(push.dispatch_key):
            raise ValueError(f"native forward push is not pending: {push.dispatch_key}")
        item.bot_push_message_id = push.id
        return pushes.get_by_dispatch_key(push.dispatch_key)

    def _require_final_sending(self, marker_token: str) -> NativeForwardBatchModel:
        batch = self.get_by_marker(marker_token)
        if batch is None or batch.status != "final_sending":
            raise ValueError("native forward batch is not final_sending")
        return batch

    def _finish_item(self, batch, item, final_message_id: int) -> None:
        if item.bot_push_message_id is None:
            raise ValueError("native forward item has no push ledger")
        push = self._session.get(BotPushMessageModel, item.bot_push_message_id)
        if push is None or push.status != "sending":
            raise ValueError("native forward push is not sending")
        relay = self._session.get(RelayMessageModel, item.relay_message_id)
        if relay is None:
            raise LookupError(f"RelayMessage 不存在: {item.relay_message_id}")
        push.status = "sent"
        push.bot_message_id = final_message_id
        item.final_bot_message_id = final_message_id
        item.status = "sent"
        self._session.add(_reply_mapping(batch.system_user_id, final_message_id, relay))

    def _mark_terminal(self, batch_id: int, status: str, code: str, detail: str) -> NativeForwardBatchModel:
        batch = self.get(batch_id)
        if batch.status in ("sent", "failed", "uncertain"):
            return batch
        batch.status = status
        batch.failure_code = code
        batch.failure_reason = detail
        self._mark_items_terminal(batch.id, status, code, detail)
        self._session.flush()
        return batch

    def _mark_items_terminal(self, batch_id: int, status: str, code: str, detail: str) -> None:
        for item in self.list_items(batch_id):
            if item.status not in ITEM_TERMINAL_STATUSES:
                item.status = status
                item.failure_code = code
                item.failure_reason = detail
            if item.bot_push_message_id is None:
                continue
            push = self._session.get(BotPushMessageModel, item.bot_push_message_id)
            if push is not None and push.status in PUSH_ACTIVE_STATUSES:
                push.status = status
                push.failure_code = code
                push.failure_reason = detail


def _reply_mapping(system_user_id: int, bot_message_id: int, relay: RelayMessageModel) -> ReplyMappingModel:
    return ReplyMappingModel(
        bot_message_id=bot_message_id,
        system_user_id=system_user_id,
        relay_message_id=relay.id,
        bound_tg_account_id=relay.bound_tg_account_id,
        peer_id=relay.peer_id,
        peer_access_hash=relay.peer_access_hash,
        source_message_id=relay.source_message_id,
        media_kind=relay.media_kind,
    )
