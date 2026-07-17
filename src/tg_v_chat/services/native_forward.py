from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Callable, Protocol

from tg_v_chat.domain import (
    DeliveryFailure,
    DeliveryUncertain,
    FirstHopForwardResult,
    IncomingPrivateBatch,
    IncomingPrivateMessage,
    NativeForwardRequest,
    TelegramPeer,
    UserSessionForwarder,
)
from tg_v_chat.storage.models import RelayMessageModel, utc_now
from tg_v_chat.storage.repositories import UnitOfWork


COLLECTION_QUIET_WINDOW = timedelta(seconds=1)
OPERATIONAL_ACCOUNT_STATUSES = frozenset({"active", "degraded"})


@dataclass(frozen=True)
class NativeForwardCollectedItem:
    batch: object
    item: object
    duplicate: bool


class NativeForwardFailureNotifier(Protocol):
    def notify_failure(self, telegram_user_id: int, text: str) -> None:
        raise NotImplementedError


class NativeForwardCollector:
    def __init__(self, uow, *, token_factory: Callable[[], str] | None = None):
        self._uow = uow
        self._token_factory = token_factory or _new_marker_token

    def ingest(self, message: IncomingPrivateMessage, *, now: datetime) -> NativeForwardCollectedItem:
        with self._uow.account_locks.acquire(message.bound_tg_account_id):
            return self._ingest_message(message, now=now)

    def ingest_batch(self, batch: IncomingPrivateBatch, *, now: datetime) -> list[NativeForwardCollectedItem]:
        messages = tuple(sorted(batch.messages, key=lambda message: message.sequence))
        if not messages:
            raise ValueError("native forward batch must contain messages")
        _validate_album_scope(messages)
        with self._uow.account_locks.acquire(messages[0].bound_tg_account_id):
            return self._ingest_album(messages, now=now)

    def _ingest_message(self, message: IncomingPrivateMessage, *, now: datetime) -> NativeForwardCollectedItem:
        account, owner = self._owner(message.bound_tg_account_id)
        relay, duplicate = self._uow.relays.create_or_get(message)
        existing = self._uow.native_forwards.get_item_by_relay(relay.id)
        if existing is not None:
            self._uow.commit()
            return NativeForwardCollectedItem(self._uow.native_forwards.get(existing.batch_id), existing, True)
        native_batch = self._collecting_batch(account, owner, message, now)
        item = self._uow.native_forwards.append_item(native_batch.id, relay.id)
        native_batch.collect_until = now + COLLECTION_QUIET_WINDOW
        self._uow.commit()
        return NativeForwardCollectedItem(native_batch, item, duplicate)

    def _ingest_album(self, messages: tuple[IncomingPrivateMessage, ...], *, now: datetime) -> list[NativeForwardCollectedItem]:
        account, owner = self._owner(messages[0].bound_tg_account_id)
        relays = [self._uow.relays.create_or_get(message)[0] for message in messages]
        existing = [self._uow.native_forwards.get_item_by_relay(relay.id) for relay in relays]
        if all(item is not None for item in existing):
            self._uow.commit()
            return [NativeForwardCollectedItem(self._uow.native_forwards.get(item.batch_id), item, True) for item in existing]
        if any(item is not None for item in existing):
            raise DeliveryUncertain("native_forward_album_inconsistent", "相册批次存在部分重复消息")
        self._uow.native_forwards.seal_due(now, account_id=account.id)
        prior = self._uow.native_forwards.find_collecting(account.id, messages[0].peer_id, now)
        if prior is not None:
            self._uow.native_forwards.seal(prior.id)
        native_batch = self._new_batch(account, owner, messages[0], now)
        items = [self._uow.native_forwards.append_item(native_batch.id, relay.id) for relay in relays]
        self._uow.native_forwards.seal(native_batch.id)
        self._uow.commit()
        return [NativeForwardCollectedItem(native_batch, item, False) for item in items]

    def _collecting_batch(self, account, owner, message: IncomingPrivateMessage, now: datetime):
        self._uow.native_forwards.seal_due(now, account_id=account.id)
        native_batch = self._uow.native_forwards.find_collecting(account.id, message.peer_id, now)
        if native_batch is not None:
            if native_batch.source_peer_access_hash is None and message.peer_access_hash is not None:
                native_batch.source_peer_access_hash = message.peer_access_hash
            return native_batch
        return self._new_batch(account, owner, message, now)

    def _new_batch(self, account, owner, message: IncomingPrivateMessage, now: datetime):
        return self._uow.native_forwards.create_collecting(
            system_user_id=owner.id,
            account_id=account.id,
            telegram_user_id=_account_identity(account),
            peer=TelegramPeer(message.peer_id, message.peer_access_hash),
            collect_until=now + COLLECTION_QUIET_WINDOW,
            marker_token=self._token_factory(),
        )

    def _owner(self, account_id: int):
        account = self._uow.accounts.get(account_id)
        if account.status not in OPERATIONAL_ACCOUNT_STATUSES:
            raise DeliveryFailure("account_unavailable", f"账号状态不可用: {account.status}")
        owner = self._uow.users.get(account.system_user_id)
        if owner.status != "active":
            raise DeliveryFailure("system_user_disabled", f"系统用户状态不可用: {owner.status}")
        _account_identity(account)
        return account, owner


class NativeForwardDispatchService:
    def __init__(
        self,
        session_factory,
        forwarder: UserSessionForwarder,
        *,
        bridge_timeout_seconds: int,
        now: Callable[[], datetime] = utc_now,
        notifier: NativeForwardFailureNotifier | None = None,
    ):
        self._session_factory = session_factory
        self._forwarder = forwarder
        self._bridge_timeout = timedelta(seconds=bridge_timeout_seconds)
        self._now = now
        self._notifier = notifier
        self._account_locks = UnitOfWork(session_factory).account_locks
        self._identity_locks = UnitOfWork(session_factory).telegram_identity_locks

    def dispatch(self, batch_id: int) -> FirstHopForwardResult | None:
        identity = self._batch_identity(batch_id)
        with self._identity_locks.acquire(identity):
            request = self._claim_bridge(batch_id)
            if request is None:
                return None
            return self._forward_claimed_batch(request)

    def dispatch_due(self, account_id: int, *, now: datetime | None = None) -> list[FirstHopForwardResult | None]:
        current = now or self._now()
        with self._account_locks.acquire(account_id):
            with UnitOfWork(self._session_factory) as uow:
                uow.native_forwards.seal_due(current, account_id=account_id)
                batch_ids = [batch.id for batch in uow.native_forwards.list_sealed_for_account(account_id)]
                uow.commit()
        return [self.dispatch(batch_id) for batch_id in batch_ids]

    def _batch_identity(self, batch_id: int) -> int:
        with UnitOfWork(self._session_factory) as uow:
            return uow.native_forwards.get(batch_id).bridge_sender_telegram_user_id

    def _claim_bridge(self, batch_id: int) -> NativeForwardRequest | None:
        with UnitOfWork(self._session_factory) as uow:
            existing = uow.native_forwards.get(batch_id)
            account = uow.accounts.get(existing.bound_tg_account_id)
            if account.status not in OPERATIONAL_ACCOUNT_STATUSES:
                uow.native_forwards.mark_failed(batch_id, "account_unavailable", f"账号状态不可用: {account.status}")
                uow.commit()
                return None
            batch = uow.native_forwards.claim_bridge(batch_id, self._now() + self._bridge_timeout)
            if batch is None:
                return None
            request = _forward_request(uow, batch)
            uow.commit()
            return request

    def _forward_claimed_batch(self, request: NativeForwardRequest) -> FirstHopForwardResult:
        try:
            result = self._forwarder.forward_batch(request)
        except DeliveryFailure as exc:
            self._mark_terminal(request.batch_id, "failed", exc.code, exc.detail)
            raise
        except DeliveryUncertain as exc:
            self._mark_terminal(request.batch_id, "uncertain", exc.code, exc.detail)
            raise
        if len(result.bridge_message_ids) != request.expected_count or len(set(result.bridge_message_ids)) != len(
            result.bridge_message_ids
        ):
            self._mark_terminal(
                request.batch_id,
                "uncertain",
                "bridge_item_count_mismatch",
                "第一跳返回消息数量与批次不一致",
            )
            return result
        try:
            with UnitOfWork(self._session_factory) as uow:
                uow.native_forwards.record_first_hop_result(
                    request.batch_id,
                    marker_message_id=result.marker_message_id,
                    bridge_message_ids=result.bridge_message_ids,
                )
                uow.commit()
        except ValueError as exc:
            self._mark_terminal(request.batch_id, "uncertain", "bridge_marker_mismatch", str(exc))
        return result

    def _mark_terminal(self, batch_id: int, status: str, code: str, detail: str) -> None:
        with UnitOfWork(self._session_factory) as uow:
            batch = uow.native_forwards.get(batch_id)
            was_terminal = batch.status in {"sent", "failed", "uncertain"}
            if status == "failed":
                batch = uow.native_forwards.mark_failed(batch_id, code, detail)
            else:
                batch = uow.native_forwards.mark_uncertain(batch_id, code, detail)
            owner_telegram_id = uow.users.get(batch.system_user_id).telegram_user_id
            uow.commit()
        if not was_terminal:
            _notify_failure(self._notifier, owner_telegram_id, code)


class NativeForwardReconciliationService:
    def __init__(self, session_factory, *, notifier: NativeForwardFailureNotifier | None = None):
        self._session_factory = session_factory
        self._notifier = notifier

    def expire(self, *, now: datetime) -> list[int]:
        with UnitOfWork(self._session_factory) as uow:
            batches = uow.native_forwards.expire_bridge_batches(now)
            owner_ids = [uow.users.get(batch.system_user_id).telegram_user_id for batch in batches]
            uow.commit()
        for owner_id in owner_ids:
            _notify_failure(self._notifier, owner_id, "bridge_timeout")
        return [batch.id for batch in batches]


def _new_marker_token() -> str:
    return token_urlsafe(24)


def _notify_failure(notifier: NativeForwardFailureNotifier | None, telegram_user_id: int, code: str) -> None:
    if notifier is None:
        return
    try:
        notifier.notify_failure(telegram_user_id, f"原生转发失败：{code}")
    except Exception as exc:
        print(f"tg-v-chat native forward notification failed for user {telegram_user_id}: {exc}")


def _account_identity(account) -> int:
    if account.telegram_user_id is None:
        raise DeliveryFailure("bound_account_identity_missing", "绑定账号尚未回填 Telegram identity")
    return account.telegram_user_id


def _validate_album_scope(messages: tuple[IncomingPrivateMessage, ...]) -> None:
    scope = {(message.bound_tg_account_id, message.peer_id) for message in messages}
    if len(scope) != 1:
        raise ValueError("native forward album must share one account and peer")


def _forward_request(uow, batch) -> NativeForwardRequest:
    items = uow.native_forwards.list_items(batch.id)
    if len(items) != batch.expected_count:
        raise ValueError("native forward batch item count does not match expected count")
    source_ids = []
    for item in items:
        relay = uow.session.get(RelayMessageModel, item.relay_message_id)
        if relay is None:
            raise LookupError(f"RelayMessage 不存在: {item.relay_message_id}")
        source_ids.append(relay.source_message_id)
    return NativeForwardRequest(
        batch_id=batch.id,
        marker_token=batch.marker_token,
        expected_count=batch.expected_count,
        source_peer=TelegramPeer(batch.source_peer_id, batch.source_peer_access_hash),
        source_message_ids=tuple(source_ids),
    )
