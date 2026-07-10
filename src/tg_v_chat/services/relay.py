from __future__ import annotations

from typing import Protocol

from tg_v_chat.domain import (
    DeliveryFailure,
    DeliveryUncertain,
    DeveloperSlot,
    IncomingPrivateBatch,
    IncomingPrivateMessage,
    IncomingRelayResult,
    OutgoingRelayResult,
    OutgoingReply,
    SessionFailure,
    SessionSlotRef,
    SessionStatus,
    TelegramPeer,
)
from tg_v_chat.services.relay_conversions import (
    message_from_relay,
    outgoing_result,
    peer_from_mapping,
    slot_ref,
    with_account_identity,
)


OPERATIONAL_ACCOUNT_STATUSES = frozenset({"active", "degraded"})
ACTIVE_MAPPING_STATUS = "active"
ACTIVE_USER_STATUS = "active"
SESSION_FAILURE_CODE = "session_failure"


class BotGateway(Protocol):
    def push_private_message(self, telegram_user_id: int, message: IncomingPrivateMessage) -> int:
        raise NotImplementedError

    def push_private_batch(self, telegram_user_id: int, batch: IncomingPrivateBatch) -> list[int]:
        raise NotImplementedError

    def notify_failure(self, telegram_user_id: int, text: str) -> None:
        raise NotImplementedError


class TelegramSenderPool(Protocol):
    def send_reply(self, session_slot, peer: TelegramPeer, reply: OutgoingReply) -> int:
        raise NotImplementedError


class PrivateRelayService:
    def __init__(self, uow, bot_gateway: BotGateway, sender_pool: TelegramSenderPool, *, media_store=None):
        self._uow = uow
        self._bot = bot_gateway
        self._senders = sender_pool
        self._media_store = media_store

    def receive_private_message(self, message: IncomingPrivateMessage) -> IncomingRelayResult:
        with self._uow.account_locks.acquire(message.bound_tg_account_id):
            return self._receive_locked(message)

    def receive_private_batch(self, batch: IncomingPrivateBatch) -> list[IncomingRelayResult]:
        if not batch.messages:
            raise ValueError("incoming batch must contain messages")
        account_id = batch.messages[0].bound_tg_account_id
        with self._uow.account_locks.acquire(account_id):
            return self._receive_batch_locked(batch)

    def _receive_locked(self, message: IncomingPrivateMessage) -> IncomingRelayResult:
        owner = self._incoming_owner(message.bound_tg_account_id)
        relay, duplicate = self._uow.relays.create_or_get(message)
        self._persist_artifacts(relay.id, message)
        if self._should_defer_album(message):
            self._uow.commit()
            return IncomingRelayResult(relay.id, None, duplicate)
        self._uow.commit()
        self._push_ready_relays(
            owner.id,
            owner.telegram_user_id,
            message=message,
            current_relay=relay,
        )
        push = self._uow.pushes.get_by_relay(relay.id)
        return IncomingRelayResult(relay.id, push.bot_message_id if push else None, duplicate)

    def ordered_media_group(self, account_id: int, media_group_id: str) -> list[int]:
        relays = self._uow.relays.list_media_group(account_id, media_group_id)
        return [relay.id for relay in relays]

    def _receive_batch_locked(self, batch: IncomingPrivateBatch) -> list[IncomingRelayResult]:
        messages = tuple(sorted(batch.messages, key=lambda item: item.sequence))
        owner = self._incoming_owner(messages[0].bound_tg_account_id)
        relays = []
        for message in messages:
            relay, _duplicate = self._uow.relays.create_or_get(message)
            self._persist_artifacts(relay.id, message)
            relays.append(relay)
        self._ensure_media_group(messages)
        self._claim_batch_pushes(owner.id, relays)
        self._uow.commit()
        bot_ids = self._send_batch(owner.telegram_user_id, IncomingPrivateBatch(messages), relays)
        return [IncomingRelayResult(relay.id, bot_id, False) for relay, bot_id in zip(relays, bot_ids)]

    def handle_bot_reply(self, reply: OutgoingReply) -> OutgoingRelayResult:
        user = self._active_user(reply.system_user_id)
        if reply.reply_to_message_id is None:
            raise ValueError("用户必须 reply Bot 推送消息才能代发")
        mapping = self._uow.mappings.get_by_bot_message(user.id, reply.reply_to_message_id)
        if not mapping:
            raise LookupError("ReplyMapping 不存在，无法代发")
        with self._uow.account_locks.acquire(mapping.bound_tg_account_id):
            self._revalidate_outbound(user, mapping)
            return self._handle_locked_reply(user.id, mapping, reply)

    def _incoming_owner(self, account_id: int):
        account = self._uow.accounts.get(account_id)
        if account.status not in OPERATIONAL_ACCOUNT_STATUSES:
            raise DeliveryFailure("account_unavailable", f"账号状态不可用: {account.status}")
        owner = self._uow.users.get(account.system_user_id)
        if owner.status != ACTIVE_USER_STATUS:
            raise DeliveryFailure("system_user_disabled", f"系统用户状态不可用: {owner.status}")
        if account.system_user_id != owner.id:
            raise DeliveryFailure("account_owner_mismatch", "账号归属校验失败")
        return owner

    def _active_user(self, telegram_user_id: int):
        user = self._uow.users.get_by_telegram_id(telegram_user_id)
        if user.status != ACTIVE_USER_STATUS:
            raise DeliveryFailure("system_user_disabled", f"系统用户状态不可用: {user.status}")
        return user

    def _revalidate_outbound(self, user, mapping):
        self._uow.session.refresh(user)
        self._uow.session.refresh(mapping)
        if user.status != ACTIVE_USER_STATUS:
            raise DeliveryFailure("system_user_disabled", f"系统用户状态不可用: {user.status}")
        if mapping.system_user_id != user.id:
            raise DeliveryFailure("mapping_owner_mismatch", "ReplyMapping 归属校验失败")
        if mapping.status != ACTIVE_MAPPING_STATUS:
            raise DeliveryFailure("mapping_expired", "ReplyMapping 已失效")
        account = self._uow.accounts.get(mapping.bound_tg_account_id)
        if account.system_user_id != user.id:
            raise DeliveryFailure("account_owner_mismatch", "账号归属校验失败")
        if account.status not in OPERATIONAL_ACCOUNT_STATUSES:
            raise DeliveryFailure("account_unavailable", f"账号状态不可用: {account.status}")
        return account

    def _handle_locked_reply(self, user_id: int, mapping, reply: OutgoingReply) -> OutgoingRelayResult:
        dispatch_key = f"outgoing:{user_id}:{reply.bot_reply_message_id}"
        existing = self._uow.outgoing.get_by_reply(user_id, reply.bot_reply_message_id)
        if existing and existing.status != "pending":
            self._discard_artifacts(reply)
            result = self._existing_outgoing(existing)
            self._uow.commit()
            return result
        if existing is None:
            existing = self._uow.outgoing.create_pending(
                reply.bot_reply_message_id,
                system_user_id=user_id,
                relay_id=mapping.relay_message_id,
                dispatch_key=dispatch_key,
            )
        self._uow.commit()
        if not self._uow.outgoing.claim(dispatch_key):
            current = self._uow.outgoing.get_by_dispatch_key(dispatch_key)
            self._discard_artifacts(reply)
            return self._existing_outgoing(current)
        self._persist_outgoing_artifacts(existing.id, reply)
        self._uow.commit()
        return self._deliver_outgoing(dispatch_key, mapping, reply)

    def _existing_outgoing(self, row) -> OutgoingRelayResult:
        if row.status == "sent":
            return outgoing_result(row, True)
        if row.status == "sending":
            row = self._uow.outgoing.mark_uncertain(
                row.dispatch_key, "stale_sending", "发送结果未知，禁止自动重发"
            )
            self._uow.commit()
        if row.status == "uncertain":
            raise DeliveryUncertain(row.failure_code or "transport_unknown", row.failure_reason or "发送结果未知")
        if row.status == "failed":
            raise DeliveryFailure(row.failure_code or "delivery_failed", row.failure_reason or "发送失败")
        raise RuntimeError(f"无法处理 outbound dispatch 状态: {row.status}")

    def _deliver_outgoing(self, dispatch_key: str, mapping, reply: OutgoingReply) -> OutgoingRelayResult:
        slots = self._healthy_slots(mapping.bound_tg_account_id)
        if not slots:
            return self._finish_exhausted_without_slots(dispatch_key, mapping.bound_tg_account_id, reply)
        for index, session_slot in enumerate(slots):
            try:
                sent_id = self._senders.send_reply(slot_ref(session_slot), peer_from_mapping(mapping), reply)
            except SessionFailure as exc:
                self._record_session_failure(slots, index, exc)
                if index + 1 < len(slots):
                    self._uow.commit()
                    continue
                return self._finish_exhausted(dispatch_key, mapping.bound_tg_account_id, exc, reply=reply)
            except DeliveryFailure as exc:
                self._uow.outgoing.mark_failed(dispatch_key, exc.code, exc.detail)
                self._release_artifacts(reply, "failed")
                self._uow.commit()
                raise
            except DeliveryUncertain as exc:
                self._uow.outgoing.mark_uncertain(dispatch_key, exc.code, exc.detail)
                self._uow.commit()
                raise
            return self._finish_sent(
                dispatch_key,
                mapping.bound_tg_account_id,
                session_slot=session_slot,
                sent_id=sent_id,
                reply=reply,
            )
        raise RuntimeError("session failover 未产生终态")

    def _record_session_failure(self, slots: list, index: int, error: SessionFailure) -> None:
        failed = slots[index]
        code = getattr(error, "code", SESSION_FAILURE_CODE)
        self._uow.sessions.mark_failed(failed.id, str(error), code=code)
        if index + 1 < len(slots):
            self._uow.failovers.create(
                failed.bound_tg_account_id,
                from_slot=DeveloperSlot(failed.developer_slot),
                to_slot=DeveloperSlot(slots[index + 1].developer_slot),
                reason=str(error),
            )
            self._uow.accounts.mark_degraded(failed.bound_tg_account_id)
            return
        self._uow.failovers.create_exhausted(
            failed.bound_tg_account_id,
            DeveloperSlot(failed.developer_slot),
            str(error),
        )

    def _finish_sent(self, dispatch_key: str, account_id: int, *, session_slot, sent_id: int, reply: OutgoingReply):
        slot = DeveloperSlot(session_slot.developer_slot)
        if slot is not DeveloperSlot.PRIMARY:
            self._uow.accounts.mark_degraded(account_id)
        row = self._uow.outgoing.mark_sent(
            dispatch_key,
            sent_message_id=sent_id,
            developer_slot=slot,
        )
        self._release_artifacts(reply, "sent")
        self._uow.commit()
        return outgoing_result(row, False)

    def _finish_exhausted(self, dispatch_key: str, account_id: int, error: SessionFailure, *, reply: OutgoingReply):
        self._uow.accounts.mark_reauth_required(account_id)
        self._uow.outgoing.mark_failed(dispatch_key, "all_sessions_unavailable", str(error))
        self._release_artifacts(reply, "failed")
        self._uow.commit()
        raise SessionFailure(f"全部 session 不可用: {error}") from error

    def _finish_exhausted_without_slots(self, dispatch_key: str, account_id: int, reply: OutgoingReply):
        detail = "全部 session 不可用: 没有可用 session"
        self._uow.accounts.mark_reauth_required(account_id)
        self._uow.outgoing.mark_failed(dispatch_key, "all_sessions_unavailable", detail)
        self._release_artifacts(reply, "failed")
        self._uow.commit()
        raise SessionFailure(detail)

    def _should_defer_album(self, message: IncomingPrivateMessage) -> bool:
        if not message.media_group_id or message.sequence <= 1:
            return False
        return not self._uow.relays.has_media_sequence(message.bound_tg_account_id, message.media_group_id, 1)

    def _push_ready_relays(self, user_id: int, telegram_user_id: int, *, message, current_relay) -> None:
        relays = [current_relay]
        if message.media_group_id:
            relays = self._uow.relays.list_media_group(message.bound_tg_account_id, message.media_group_id)
        for relay in relays:
            current = message if relay.id == current_relay.id else None
            self._dispatch_push(user_id, telegram_user_id, relay, message=current)

    def _dispatch_push(self, user_id: int, telegram_user_id: int, relay, *, message=None) -> None:
        dispatch_key = f"push:{relay.id}"
        existing = self._uow.pushes.get_by_relay(relay.id)
        if existing and existing.status != "pending":
            if message is not None:
                self._discard_artifacts(message)
            self._existing_push(existing)
            self._uow.commit()
            return
        if existing is None:
            self._uow.pushes.create_pending(relay.id, user_id, dispatch_key)
            self._uow.commit()
        if not self._uow.pushes.claim(dispatch_key):
            if message is not None:
                self._discard_artifacts(message)
            self._existing_push(self._uow.pushes.get_by_dispatch_key(dispatch_key))
            return
        self._uow.commit()
        self._send_push(dispatch_key, user_id, telegram_user_id=telegram_user_id, relay=relay, message=message)

    def _existing_push(self, row) -> None:
        if row.status == "sent":
            return
        if row.status == "sending":
            row = self._uow.pushes.mark_uncertain(
                row.dispatch_key, "stale_sending", "Bot 推送结果未知，禁止自动重发"
            )
            self._uow.commit()
        if row.status == "uncertain":
            raise DeliveryUncertain(row.failure_code or "transport_unknown", row.failure_reason or "Bot 推送结果未知")
        if row.status == "failed":
            raise DeliveryFailure(row.failure_code or "bot_push_failed", row.failure_reason or "Bot 推送失败")
        raise RuntimeError(f"无法处理 Bot push dispatch 状态: {row.status}")

    def _send_push(self, dispatch_key: str, user_id: int, *, telegram_user_id: int, relay, message=None) -> None:
        account = self._uow.accounts.get(relay.bound_tg_account_id)
        push_message = with_account_identity(message, account) if message else message_from_relay(relay, account)
        try:
            bot_message_id = self._bot.push_private_message(telegram_user_id, push_message)
        except DeliveryUncertain as exc:
            self._uow.pushes.mark_uncertain(dispatch_key, exc.code, exc.detail)
            self._uow.commit()
            raise
        except DeliveryFailure as exc:
            self._uow.pushes.mark_failed(dispatch_key, exc.code, exc.detail)
            self._release_artifacts(push_message, "failed")
            self._uow.commit()
            raise
        except Exception as exc:
            self._uow.pushes.mark_failed(dispatch_key, "bot_push_failed", str(exc))
            self._release_artifacts(push_message, "failed")
            self._uow.commit()
            raise DeliveryFailure("bot_push_failed", str(exc)) from exc
        self._uow.pushes.mark_sent(dispatch_key, bot_message_id=bot_message_id)
        self._uow.mappings.create(bot_message_id, relay, user_id)
        self._release_artifacts(push_message, "sent")
        self._uow.commit()

    def _persist_artifacts(self, relay_id: int, message: IncomingPrivateMessage) -> None:
        for artifact in message.artifacts:
            if self._uow.media_artifacts.get_by_storage_key(artifact.storage_key):
                continue
            self._uow.media_artifacts.create(
                direction="incoming",
                relay_message_id=relay_id,
                storage_key=artifact.storage_key,
                file_name=artifact.file_name,
                mime_type=artifact.mime_type,
                byte_size=artifact.byte_size,
                media_kind=artifact.media_kind,
                sequence=artifact.sequence,
                metadata_json=artifact.metadata_json,
            )
            self._uow.media_artifacts.mark_ready(artifact.storage_key)

    def _persist_outgoing_artifacts(self, outgoing_id: int, reply: OutgoingReply) -> None:
        for artifact in reply.artifacts:
            if self._uow.media_artifacts.get_by_storage_key(artifact.storage_key):
                continue
            self._uow.media_artifacts.create(
                direction="outgoing",
                outgoing_reply_id=outgoing_id,
                storage_key=artifact.storage_key,
                file_name=artifact.file_name,
                mime_type=artifact.mime_type,
                byte_size=artifact.byte_size,
                media_kind=artifact.media_kind,
                sequence=artifact.sequence,
                metadata_json=artifact.metadata_json,
            )
            self._uow.media_artifacts.mark_ready(artifact.storage_key)

    def _ensure_media_group(self, messages: tuple[IncomingPrivateMessage, ...]) -> None:
        group_id = messages[0].media_group_id
        if not group_id:
            return
        account_id = messages[0].bound_tg_account_id
        if self._uow.media_groups.get_for_account(account_id, group_id):
            return
        self._uow.media_groups.create(
            account_id,
            media_group_id=group_id,
            item_count=len(messages),
            dispatch_key=f"album:{account_id}:{group_id}",
        )

    def _claim_batch_pushes(self, user_id: int, relays: list) -> None:
        for relay in relays:
            key = f"push:{relay.id}"
            existing = self._uow.pushes.get_by_relay(relay.id)
            if existing and existing.status != "pending":
                self._existing_push(existing)
                continue
            if existing is None:
                self._uow.pushes.create_pending(relay.id, user_id, key)
            if not self._uow.pushes.claim(key):
                self._existing_push(self._uow.pushes.get_by_dispatch_key(key))

    def _send_batch(self, telegram_user_id: int, batch: IncomingPrivateBatch, relays: list) -> list[int]:
        try:
            bot_ids = self._bot.push_private_batch(telegram_user_id, batch)
            if len(bot_ids) != len(relays):
                raise DeliveryFailure("bot_push_count_mismatch", "Bot returned wrong album result count")
        except DeliveryUncertain as exc:
            self._mark_batch_uncertain(relays, exc)
            raise
        except DeliveryFailure as exc:
            self._mark_batch_failed(relays, exc, batch.messages)
            raise
        self._finish_batch_sent(relays, bot_ids, batch.messages)
        return bot_ids

    def _finish_batch_sent(self, relays: list, bot_ids: list[int], messages: tuple) -> None:
        for relay, bot_id, message in zip(relays, bot_ids, messages):
            key = f"push:{relay.id}"
            self._uow.pushes.mark_sent(key, bot_message_id=bot_id)
            user_id = self._uow.pushes.get_by_relay(relay.id).system_user_id
            self._uow.mappings.create(bot_id, relay, user_id)
            self._release_artifacts(message, "sent")
        self._uow.commit()

    def _mark_batch_uncertain(self, relays: list, error: DeliveryUncertain) -> None:
        for relay in relays:
            self._uow.pushes.mark_uncertain(f"push:{relay.id}", error.code, error.detail)
        self._uow.commit()

    def _mark_batch_failed(self, relays: list, error: DeliveryFailure, messages: tuple) -> None:
        for relay, message in zip(relays, messages):
            self._uow.pushes.mark_failed(f"push:{relay.id}", error.code, error.detail)
            self._release_artifacts(message, "failed")
        self._uow.commit()

    def _release_artifacts(self, message: IncomingPrivateMessage, dispatch_status: str) -> None:
        if self._media_store is None:
            return
        for artifact in message.artifacts:
            row = self._uow.media_artifacts.get_by_storage_key(artifact.storage_key)
            if row is None or row.status == "released":
                continue
            self._mark_artifact_terminal(artifact.storage_key, row.status, dispatch_status)
            if self._media_store.release_for_dispatch(artifact, dispatch_status):
                self._uow.media_artifacts.mark_released(artifact.storage_key)

    def _discard_artifacts(self, message) -> None:
        if self._media_store is None:
            return
        for artifact in message.artifacts:
            self._media_store.discard(artifact)
            row = self._uow.media_artifacts.get_by_storage_key(artifact.storage_key)
            if row is None or row.status == "released":
                continue
            if row.status in {"staging", "ready"}:
                self._uow.media_artifacts.mark_failed(artifact.storage_key, "duplicate_artifact_discarded")
            row = self._uow.media_artifacts.get_by_storage_key(artifact.storage_key)
            if row.status in {"sent", "failed"}:
                self._uow.media_artifacts.mark_released(artifact.storage_key)

    def _mark_artifact_terminal(self, storage_key: str, status: str, dispatch_status: str) -> None:
        if dispatch_status == "sent" and status == "ready":
            self._uow.media_artifacts.mark_sent(storage_key)
        if dispatch_status == "failed" and status in {"staging", "ready"}:
            self._uow.media_artifacts.mark_failed(storage_key, "bot_push_failed")

    def _healthy_slots(self, account_id: int) -> list:
        usable = {SessionStatus.ACTIVE.value, SessionStatus.STANDBY.value}
        return [slot for slot in self._uow.sessions.list_for_account(account_id) if slot.status in usable]
