from datetime import datetime, timedelta, timezone

import pytest

from tg_v_chat.domain import DeliveryFailure, FirstHopForwardResult, IncomingPrivateBatch, IncomingPrivateMessage, MediaKind
from tg_v_chat.services.native_forward import (
    NativeForwardCollector,
    NativeForwardDispatchService,
    NativeForwardReconciliationService,
)
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork


class Clock:
    def __init__(self):
        self.value = datetime(2026, 7, 17, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)
        return self.value


class Forwarder:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def forward_batch(self, request):
        self.requests.append(request)
        return self.result


class Notifier:
    def __init__(self):
        self.calls = []

    def notify_failure(self, telegram_user_id, text):
        self.calls.append((telegram_user_id, text))


class RecordingLock:
    def __init__(self, calls, account_id):
        self._calls = calls
        self._account_id = account_id

    def __enter__(self):
        self._calls.append(self._account_id)
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


def _factory():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    return factory


def _message(account_id: int, source_id: int, *, peer_id: int = 88) -> IncomingPrivateMessage:
    return IncomingPrivateMessage(
        account_id,
        peer_id,
        source_id,
        MediaKind.TEXT,
        f"message-{source_id}",
        None,
        1,
        peer_access_hash=9901,
    )


def _account(factory):
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(1001)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.accounts.mark_active(account.id)
        uow.accounts.update_telegram_identity(account.id, 7001)
        uow.commit()
        return account


def test_three_normal_messages_receive_distinct_batch_sequence():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    with UnitOfWork(factory) as uow:
        collector = NativeForwardCollector(uow, token_factory=lambda: "marker-1")
        first = collector.ingest(_message(account.id, 11), now=clock.now())
        second = collector.ingest(_message(account.id, 12), now=clock.advance(milliseconds=100))
        third = collector.ingest(_message(account.id, 13), now=clock.advance(milliseconds=100))

        assert [result.item.batch_sequence for result in (first, second, third)] == [1, 2, 3]
        assert len({result.batch.id for result in (first, second, third)}) == 1


def test_same_source_message_id_from_different_peers_is_not_deduplicated():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    with UnitOfWork(factory) as uow:
        collector = NativeForwardCollector(uow, token_factory=lambda: f"marker-{clock.now().timestamp()}")
        first = collector.ingest(_message(account.id, 11, peer_id=88), now=clock.now())
        second = collector.ingest(_message(account.id, 11, peer_id=99), now=clock.advance(milliseconds=100))

        assert not first.duplicate
        assert not second.duplicate
        assert first.batch.id != second.batch.id
        assert (first.batch.source_peer_id, second.batch.source_peer_id) == (88, 99)


def test_message_101_starts_an_adjacent_batch_without_dropping_the_first_100():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    tokens = iter(("marker-1", "marker-2"))
    with UnitOfWork(factory) as uow:
        collector = NativeForwardCollector(uow, token_factory=lambda: next(tokens))
        collected = [collector.ingest(_message(account.id, source_id), now=clock.now()) for source_id in range(1, 102)]

        first_batch = uow.native_forwards.get(collected[0].batch.id)
        second_batch = uow.native_forwards.get(collected[-1].batch.id)
        assert first_batch.id != second_batch.id
        assert first_batch.expected_count == 100
        assert second_batch.expected_count == 1
        assert collected[-1].item.batch_sequence == 1


def test_first_hop_count_mismatch_becomes_uncertain():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    with UnitOfWork(factory) as uow:
        collector = NativeForwardCollector(uow, token_factory=lambda: "marker-1")
        first = collector.ingest(_message(account.id, 11), now=clock.now())
        collector.ingest(_message(account.id, 12), now=clock.advance(milliseconds=100))
        uow.native_forwards.seal(first.batch.id)
        uow.commit()

    forwarder = Forwarder(FirstHopForwardResult(1))
    dispatcher = NativeForwardDispatchService(factory, forwarder, bridge_timeout_seconds=30, now=clock.now)

    dispatcher.dispatch(first.batch.id)

    assert forwarder.requests[0].source_message_ids == (11, 12)
    with UnitOfWork(factory) as uow:
        batch = uow.native_forwards.get(first.batch.id)
        assert batch.status == "uncertain"
        assert batch.failure_code == "bridge_item_count_mismatch"


def test_first_hop_keeps_bot_marker_when_bot_receives_it_before_rpc_returns():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    with UnitOfWork(factory) as uow:
        received = NativeForwardCollector(uow, token_factory=lambda: "marker-1").ingest(
            _message(account.id, 11),
            now=clock.now(),
        )
        uow.native_forwards.seal(received.batch.id)
        uow.commit()

    class MarkerFirstForwarder:
        def forward_batch(self, request):
            with UnitOfWork(factory) as uow:
                batch = uow.native_forwards.mark_awaiting_bot(
                    request.marker_token,
                    7001,
                    marker_message_id=1600,
                )
                assert batch is not None
                uow.commit()
            return FirstHopForwardResult(forwarded_count=request.expected_count)

    NativeForwardDispatchService(
        factory,
        MarkerFirstForwarder(),
        bridge_timeout_seconds=30,
        now=clock.now,
    ).dispatch(received.batch.id)

    with UnitOfWork(factory) as uow:
        batch = uow.native_forwards.get(received.batch.id)
        assert batch.status == "awaiting_bot"
        assert batch.first_hop_marker_message_id == 1600


def test_dispatch_due_seals_batches_inside_the_collector_account_lock(monkeypatch):
    factory = _factory()
    dispatcher = NativeForwardDispatchService(
        factory,
        Forwarder(FirstHopForwardResult(1)),
        bridge_timeout_seconds=30,
    )
    acquired = []
    monkeypatch.setattr(
        dispatcher._account_locks,
        "acquire",
        lambda account_id: RecordingLock(acquired, account_id),
    )

    assert dispatcher.dispatch_due(42) == []
    assert acquired == [42]


def test_message_after_quiet_window_seals_prior_collecting_batch():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    with UnitOfWork(factory) as uow:
        collector = NativeForwardCollector(uow, token_factory=lambda: f"marker-{clock.now().timestamp()}")
        first = collector.ingest(_message(account.id, 11), now=clock.now())
        second = collector.ingest(_message(account.id, 12), now=clock.advance(seconds=2))

        assert first.batch.id != second.batch.id
        assert uow.native_forwards.get(first.batch.id).status == "sealed"
        assert uow.native_forwards.get(second.batch.id).status == "collecting"


def test_album_after_quiet_window_seals_prior_collecting_batch():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    album = IncomingPrivateBatch(
        (
            IncomingPrivateMessage(account.id, 88, 12, MediaKind.PHOTO, "one", "album-1", 1, 9901),
            IncomingPrivateMessage(account.id, 88, 13, MediaKind.VIDEO, "two", "album-1", 2, 9901),
        )
    )
    with UnitOfWork(factory) as uow:
        collector = NativeForwardCollector(uow, token_factory=lambda: f"marker-{clock.now().timestamp()}")
        first = collector.ingest(_message(account.id, 11), now=clock.now())
        collected = collector.ingest_batch(album, now=clock.advance(seconds=2))

        assert uow.native_forwards.get(first.batch.id).status == "sealed"
        assert collected[0].batch.id != first.batch.id
        assert uow.native_forwards.get(collected[0].batch.id).status == "sealed"


def test_expired_awaiting_bot_becomes_uncertain():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    with UnitOfWork(factory) as uow:
        collector = NativeForwardCollector(uow, token_factory=lambda: "marker-1")
        received = collector.ingest(_message(account.id, 11), now=clock.now())
        uow.native_forwards.seal(received.batch.id)
        uow.native_forwards.claim_bridge(received.batch.id, clock.advance(seconds=30))
        uow.native_forwards.mark_awaiting_bot("marker-1", 7001, marker_message_id=500)
        uow.commit()

    reconciliation = NativeForwardReconciliationService(factory)

    reconciliation.expire(now=clock.advance(seconds=1))

    with UnitOfWork(factory) as uow:
        batch = uow.native_forwards.get(received.batch.id)
        assert batch.status == "uncertain"
        assert batch.failure_code == "bridge_timeout"


def test_expired_bridge_notifies_the_batch_owner_without_private_payload():
    factory = _factory()
    account = _account(factory)
    clock = Clock()
    with UnitOfWork(factory) as uow:
        collector = NativeForwardCollector(uow, token_factory=lambda: "marker-1")
        received = collector.ingest(_message(account.id, 11), now=clock.now())
        uow.native_forwards.seal(received.batch.id)
        uow.native_forwards.claim_bridge(received.batch.id, clock.advance(seconds=30))
        uow.commit()

    notifier = Notifier()
    NativeForwardReconciliationService(factory, notifier=notifier).expire(now=clock.advance(seconds=1))

    assert notifier.calls == [(1001, "原生转发失败：bridge_timeout")]


def test_first_hop_failure_notifies_the_batch_owner():
    class FailingForwarder:
        def forward_batch(self, _request):
            raise DeliveryFailure("native_forward_restricted", "protected content")

    factory = _factory()
    account = _account(factory)
    clock = Clock()
    with UnitOfWork(factory) as uow:
        received = NativeForwardCollector(uow, token_factory=lambda: "marker-1").ingest(
            _message(account.id, 11),
            now=clock.now(),
        )
        uow.native_forwards.seal(received.batch.id)
        uow.commit()

    notifier = Notifier()
    dispatcher = NativeForwardDispatchService(
        factory,
        FailingForwarder(),
        bridge_timeout_seconds=30,
        now=clock.now,
        notifier=notifier,
    )

    with pytest.raises(DeliveryFailure, match="native_forward_restricted"):
        dispatcher.dispatch(received.batch.id)

    assert notifier.calls == [(1001, "原生转发失败：native_forward_restricted")]
