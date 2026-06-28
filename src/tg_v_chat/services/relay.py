from __future__ import annotations

from typing import Protocol

from tg_v_chat.domain import (
    DeveloperSlot,
    IncomingPrivateMessage,
    IncomingRelayResult,
    OutgoingRelayResult,
    OutgoingReply,
    SessionFailure,
    SessionSlotRef,
    SessionStatus,
)


class BotGateway(Protocol):
    def push_private_message(self, system_user_id: int, message: IncomingPrivateMessage) -> int:
        raise NotImplementedError

    def notify_failure(self, system_user_id: int, text: str) -> None:
        raise NotImplementedError


class TelegramSenderPool(Protocol):
    def send_reply(self, session_slot, peer_id: int, reply: OutgoingReply) -> int:
        raise NotImplementedError


class PrivateRelayService:
    def __init__(self, uow, bot_gateway: BotGateway, sender_pool: TelegramSenderPool):
        self._uow = uow
        self._bot = bot_gateway
        self._senders = sender_pool

    def receive_private_message(self, message: IncomingPrivateMessage) -> IncomingRelayResult:
        relay, duplicate = self._uow.relays.create_or_get(message)
        if duplicate:
            return self._existing_incoming_result(relay)
        if self._should_defer_album(message):
            self._uow.commit()
            return IncomingRelayResult(relay.id, None, False)
        account = self._uow.accounts.get(message.bound_tg_account_id)
        self._push_ready_relays(account.system_user_id, message, relay)
        self._uow.commit()
        push = self._uow.pushes.get_by_relay(relay.id)
        return IncomingRelayResult(relay.id, push.bot_message_id if push else None, False)

    def ordered_media_group(self, account_id: int, media_group_id: str) -> list[int]:
        relays = self._uow.relays.list_media_group(account_id, media_group_id)
        return [relay.id for relay in relays]

    def handle_bot_reply(self, reply: OutgoingReply) -> OutgoingRelayResult:
        if reply.reply_to_message_id is None:
            raise ValueError("用户必须 reply Bot 推送消息才能代发")
        existing = self._uow.outgoing.get_by_reply(reply.bot_reply_message_id)
        if existing:
            if existing.system_user_id != reply.system_user_id:
                raise PermissionError("无权使用其他用户的 outgoing reply")
            return _outgoing_result(existing, True)
        mapping = self._uow.mappings.get_by_bot_message(reply.reply_to_message_id)
        if not mapping:
            raise LookupError("ReplyMapping 不存在，无法代发")
        if mapping.system_user_id != reply.system_user_id:
            raise PermissionError("无权使用其他用户的 ReplyMapping")
        sent_id, slot = self._send_with_failover(mapping, reply)
        row = self._uow.outgoing.create(
            reply.bot_reply_message_id,
            reply.system_user_id,
            mapping.relay_message_id,
            sent_id,
            slot,
        )
        self._uow.commit()
        return _outgoing_result(row, False)

    def _existing_incoming_result(self, relay) -> IncomingRelayResult:
        push = self._uow.pushes.get_by_relay(relay.id)
        return IncomingRelayResult(relay.id, push.bot_message_id if push else None, True)

    def _should_defer_album(self, message: IncomingPrivateMessage) -> bool:
        if not message.media_group_id or message.sequence <= 1:
            return False
        return not self._uow.relays.has_media_sequence(message.bound_tg_account_id, message.media_group_id, 1)

    def _push_ready_relays(self, system_user_id: int, message: IncomingPrivateMessage, current_relay) -> None:
        relays = [current_relay]
        if message.media_group_id:
            relays = self._uow.relays.list_media_group(message.bound_tg_account_id, message.media_group_id)
        for relay in relays:
            if self._uow.pushes.get_by_relay(relay.id):
                continue
            self._push_relay(system_user_id, relay)

    def _push_relay(self, system_user_id: int, relay) -> None:
        message = _message_from_relay(relay)
        bot_message_id = self._bot.push_private_message(system_user_id, message)
        self._uow.pushes.create(relay.id, system_user_id, bot_message_id)
        self._uow.mappings.create(bot_message_id, relay, system_user_id)

    def _send_with_failover(self, mapping, reply: OutgoingReply) -> tuple[int, DeveloperSlot]:
        slots = self._healthy_slots(mapping.bound_tg_account_id)
        last_error = None
        for index, session_slot in enumerate(slots):
            try:
                slot_ref = _slot_ref(session_slot)
                return self._senders.send_reply(slot_ref, mapping.peer_id, reply), slot_ref.developer_slot
            except SessionFailure as exc:
                last_error = exc
                self._mark_failed_and_record(slots, index, str(exc))
        raise SessionFailure(f"全部 session 不可用: {last_error}")

    def _healthy_slots(self, account_id: int) -> list:
        healthy = []
        for slot in self._uow.sessions.list_for_account(account_id):
            if slot.status in {SessionStatus.ACTIVE.value, SessionStatus.STANDBY.value}:
                healthy.append(slot)
        if not healthy:
            raise SessionFailure("全部 session 不可用")
        return healthy

    def _mark_failed_and_record(self, slots: list, index: int, reason: str) -> None:
        failed = slots[index]
        self._uow.sessions.mark_failed(failed.id, reason)
        if index + 1 < len(slots):
            self._uow.failovers.create(
                failed.bound_tg_account_id,
                DeveloperSlot(failed.developer_slot),
                DeveloperSlot(slots[index + 1].developer_slot),
                reason,
            )
            return
        self._uow.failovers.create_exhausted(
            failed.bound_tg_account_id,
            DeveloperSlot(failed.developer_slot),
            reason,
        )


def _outgoing_result(row, duplicate: bool) -> OutgoingRelayResult:
    return OutgoingRelayResult(
        relay_message_id=row.relay_message_id,
        sent_message_id=row.sent_message_id,
        developer_slot=DeveloperSlot(row.developer_slot),
        duplicate=duplicate,
    )


def _slot_ref(row) -> SessionSlotRef:
    return SessionSlotRef(
        id=row.id,
        bound_tg_account_id=row.bound_tg_account_id,
        developer_slot=DeveloperSlot(row.developer_slot),
        encrypted_session=row.encrypted_session,
    )


def _message_from_relay(row) -> IncomingPrivateMessage:
    from tg_v_chat.domain import MediaKind

    return IncomingPrivateMessage(
        bound_tg_account_id=row.bound_tg_account_id,
        peer_id=row.peer_id,
        source_message_id=row.source_message_id,
        media_kind=MediaKind(row.media_kind),
        payload=row.payload,
        media_group_id=row.media_group_id,
        sequence=row.sequence,
    )
