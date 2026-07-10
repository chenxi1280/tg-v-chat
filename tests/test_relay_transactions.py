from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tg_v_chat.domain import (
    DeliveryFailure,
    DeliveryUncertain,
    DeveloperSlot,
    IncomingPrivateMessage,
    MediaKind,
    OutgoingReply,
    SessionFailure,
    SessionSlotRef,
    SessionStatus,
    TelegramPeer,
)
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.models import OutgoingReplyModel
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.telethon_clients.gateway import (
    TelethonBotGateway,
    TelethonReplySender,
    TelethonSenderPool,
)


class RecordingBot:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def push_private_message(self, _telegram_user_id, _message):
        self.calls += 1
        if self.error:
            raise self.error
        return 500 + self.calls

    def notify_failure(self, _telegram_user_id, _text):
        raise AssertionError("unexpected failure notification")


class RecordingSender:
    def __init__(self, failures=None):
        self.failures = failures or {}
        self.calls = []

    def send_reply(self, session_slot, _peer, _reply):
        self.calls.append(session_slot.developer_slot)
        failure = self.failures.get(session_slot.developer_slot)
        if failure:
            raise failure
        return 9000 + len(self.calls)


@pytest.fixture()
def factory():
    session_factory = create_session_factory("sqlite:///:memory:")
    init_db(session_factory)
    return session_factory


def _seed_mapping(factory, telegram_user_id=1001):
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(telegram_user_id)
        account = uow.accounts.create(user.id, "+15550000001")
        for slot in DeveloperSlot:
            status = SessionStatus.ACTIVE if slot is DeveloperSlot.PRIMARY else SessionStatus.STANDBY
            uow.sessions.create(account.id, slot=slot, encrypted_session="encrypted", status=status)
        uow.accounts.mark_active(account.id)
        uow.commit()
        result = PrivateRelayService(uow, RecordingBot(), RecordingSender()).receive_private_message(
            IncomingPrivateMessage(account.id, 88, 101, MediaKind.TEXT, "hi", None, 0)
        )
        return account.id, result.bot_message_id


def _reply(bot_message_id, reply_id=700):
    return OutgoingReply(1001, reply_id, bot_message_id, MediaKind.TEXT, "reply")


@pytest.mark.parametrize("account_status", ["disabled", "deleted", "reauth_required"])
def test_outbound_revalidates_account_in_new_uow_before_sender(factory, account_status):
    account_id, bot_message_id = _seed_mapping(factory)
    with UnitOfWork(factory) as uow:
        getattr(uow.accounts, f"mark_{account_status}")(account_id)
        uow.commit()
    sender = RecordingSender()

    with pytest.raises(DeliveryFailure, match="account_unavailable"):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(_reply(bot_message_id))

    assert sender.calls == []


def test_outbound_rejects_disabled_user_and_expired_mapping(factory):
    account_id, bot_message_id = _seed_mapping(factory)
    sender = RecordingSender()
    with UnitOfWork(factory) as uow:
        uow.mappings.invalidate_for_account(account_id)
        uow.commit()
    with pytest.raises(DeliveryFailure, match="mapping_expired"):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(_reply(bot_message_id))
    with UnitOfWork(factory) as uow:
        uow.users.mark_disabled(1)
        uow.commit()
    with pytest.raises(DeliveryFailure, match="system_user_disabled"):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(_reply(bot_message_id, 701))
    assert sender.calls == []


def test_all_session_failures_commit_exhaustion_before_exception(factory):
    account_id, bot_message_id = _seed_mapping(factory)
    failures = {slot: SessionFailure(f"{slot.value} failed") for slot in DeveloperSlot}

    with pytest.raises(SessionFailure, match="全部 session 不可用"):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(uow, RecordingBot(), RecordingSender(failures)).handle_bot_reply(
                _reply(bot_message_id)
            )

    with UnitOfWork(factory) as uow:
        slots = uow.sessions.list_for_account(account_id)
        events = uow.failovers.list_for_account(account_id)
        outgoing = uow.outgoing.get_by_reply(1, 700)
        assert all(slot.status == "failed" and slot.failure_code for slot in slots)
        assert [event.status for event in events] == ["switched", "switched", "exhausted"]
        assert events[-1].to_slot is None
        assert uow.accounts.get(account_id).status == "reauth_required"
        assert outgoing.status == "failed"


def test_standby_success_commits_degraded_account_and_switch_event(factory):
    account_id, bot_message_id = _seed_mapping(factory)
    sender = RecordingSender({DeveloperSlot.PRIMARY: SessionFailure("primary expired")})
    with UnitOfWork(factory) as uow:
        result = PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(_reply(bot_message_id))

    with UnitOfWork(factory) as uow:
        primary = uow.sessions.get_for_account(account_id, DeveloperSlot.PRIMARY)
        outgoing = uow.outgoing.get_by_reply(1, 700)
        assert primary.status == "failed" and primary.failure_reason == "primary expired"
        assert uow.accounts.get(account_id).status == "degraded"
        assert [event.status for event in uow.failovers.list_for_account(account_id)] == ["switched"]
        assert outgoing.status == "sent"
    assert result.developer_slot is DeveloperSlot.STANDBY_1


@pytest.mark.parametrize(
    ("standby_error", "terminal_status"),
    [
        (DeliveryFailure("peer_invalid", "peer unavailable"), "failed"),
        (DeliveryUncertain("transport_unknown", "result unknown"), "uncertain"),
    ],
)
def test_primary_failure_with_usable_standby_persists_degraded_account(
    factory, standby_error, terminal_status
):
    account_id, bot_message_id = _seed_mapping(factory)
    sender = RecordingSender(
        {
            DeveloperSlot.PRIMARY: SessionFailure("primary expired"),
            DeveloperSlot.STANDBY_1: standby_error,
        }
    )

    with pytest.raises(type(standby_error)):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(_reply(bot_message_id))

    with UnitOfWork(factory) as uow:
        assert uow.accounts.get(account_id).status == "degraded"
        assert uow.outgoing.get_by_reply(1, 700).status == terminal_status
        assert [event.status for event in uow.failovers.list_for_account(account_id)] == ["switched"]
    assert sender.calls == [DeveloperSlot.PRIMARY, DeveloperSlot.STANDBY_1]


def test_delivery_failure_is_terminal_without_failover(factory):
    account_id, bot_message_id = _seed_mapping(factory)
    failure = DeliveryFailure("peer_invalid", "peer unavailable")
    with pytest.raises(DeliveryFailure) as raised:
        with UnitOfWork(factory) as uow:
            PrivateRelayService(
                uow, RecordingBot(), RecordingSender({DeveloperSlot.PRIMARY: failure})
            ).handle_bot_reply(_reply(bot_message_id))

    with UnitOfWork(factory) as uow:
        assert raised.value.code == "peer_invalid"
        assert uow.outgoing.get_by_reply(1, 700).status == "failed"
        assert uow.failovers.list_for_account(account_id) == []
        assert [slot.status for slot in uow.sessions.list_for_account(account_id)] == [
            "active", "standby", "standby"
        ]


def test_uncertain_and_failed_duplicates_never_resend(factory):
    _account_id, bot_message_id = _seed_mapping(factory)
    sender = RecordingSender({DeveloperSlot.PRIMARY: DeliveryUncertain("transport_unknown", "lost")})
    for _attempt in range(2):
        with pytest.raises(DeliveryUncertain):
            with UnitOfWork(factory) as uow:
                PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(_reply(bot_message_id))
    assert sender.calls == [DeveloperSlot.PRIMARY]

    with UnitOfWork(factory) as uow:
        row = uow.outgoing.get_by_reply(1, 700)
        assert row.status == "uncertain"


def test_stale_sending_and_failed_dispatches_do_not_resend(factory):
    _account_id, bot_message_id = _seed_mapping(factory)
    with UnitOfWork(factory) as uow:
        mapping = uow.mappings.get_by_bot_message(1, bot_message_id)
        uow.outgoing.create_pending(702, system_user_id=1, relay_id=mapping.relay_message_id, dispatch_key="outgoing:1:702")
        assert uow.outgoing.claim("outgoing:1:702")
        uow.commit()
    sender = RecordingSender()
    with pytest.raises(DeliveryUncertain):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(_reply(bot_message_id, 702))

    with UnitOfWork(factory) as uow:
        uow.outgoing.create_pending(703, system_user_id=1, relay_id=1, dispatch_key="outgoing:1:703")
        assert uow.outgoing.claim("outgoing:1:703")
        uow.outgoing.mark_failed("outgoing:1:703", "peer_invalid", "stored failure")
        uow.commit()
    with pytest.raises(DeliveryFailure, match="stored failure"):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(_reply(bot_message_id, 703))
    assert sender.calls == []


def test_uncertain_bot_push_is_durable_and_not_repeated(factory):
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(1001)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.accounts.mark_active(account.id)
        uow.commit()
        account_id = account.id
    message = IncomingPrivateMessage(account_id, 88, 111, MediaKind.TEXT, "hi", None, 0)
    bot = RecordingBot(DeliveryUncertain("transport_unknown", "lost"))
    for _attempt in range(2):
        with pytest.raises(DeliveryUncertain):
            with UnitOfWork(factory) as uow:
                PrivateRelayService(uow, bot, RecordingSender()).receive_private_message(message)
    with UnitOfWork(factory) as uow:
        assert uow.pushes.get_by_relay(1).status == "uncertain"
    assert bot.calls == 1


class FakeTelethonClient:
    connect_error = None
    send_error = None
    disconnect_error = None

    def __init__(self, *_args):
        pass

    async def connect(self):
        if self.connect_error:
            raise self.connect_error

    async def send_message(self, _peer, _payload):
        if self.send_error:
            raise self.send_error
        return SimpleNamespace(id=77)

    async def disconnect(self):
        if self.disconnect_error:
            raise self.disconnect_error
        return None


def _run_gateway(
    monkeypatch,
    *,
    connect_error=None,
    send_error=None,
    session_error=None,
    disconnect_error=None,
):
    import telethon
    import telethon.sessions

    FakeTelethonClient.connect_error = connect_error
    FakeTelethonClient.send_error = send_error
    FakeTelethonClient.disconnect_error = disconnect_error
    monkeypatch.setattr(telethon, "TelegramClient", FakeTelethonClient)
    def build_session(value):
        if session_error:
            raise session_error
        return value

    monkeypatch.setattr(telethon.sessions, "StringSession", build_session)
    sender = TelethonReplySender({DeveloperSlot.PRIMARY: SimpleNamespace(api_id=1, api_hash="x")}, None)
    slot = SessionSlotRef(1, 1, DeveloperSlot.PRIMARY, "encrypted")
    reply = OutgoingReply(1001, 700, 500, MediaKind.TEXT, "reply")
    return asyncio.run(sender._send(slot, session_string="session", peer=TelegramPeer(88), reply=reply))


def test_gateway_classifies_connect_and_authorization_as_session_failure(monkeypatch):
    from telethon.errors import AuthKeyUnregisteredError

    with pytest.raises(SessionFailure):
        _run_gateway(monkeypatch, connect_error=ConnectionError("offline"))
    with pytest.raises(SessionFailure):
        _run_gateway(monkeypatch, send_error=AuthKeyUnregisteredError(None))
    with pytest.raises(SessionFailure):
        _run_gateway(monkeypatch, session_error=ValueError("invalid session"))


@pytest.mark.parametrize("error", [ConnectionError("drop"), OSError("io"), TimeoutError("late")])
def test_gateway_classifies_send_transport_as_uncertain(monkeypatch, error):
    with pytest.raises(DeliveryUncertain, match="transport_unknown"):
        _run_gateway(monkeypatch, send_error=error)


def test_gateway_classifies_business_rpc_and_unknown_send_as_delivery_failure(monkeypatch):
    from telethon.errors import ChatWriteForbiddenError, FloodWaitError, PeerIdInvalidError, RPCError

    cases = (
        (PeerIdInvalidError(None), "peer_invalid"),
        (ChatWriteForbiddenError(None), "permission_denied"),
        (FloodWaitError(None, 3), "flood_wait"),
        (RPCError(None, "bad", 400), "telegram_rpc"),
        (RuntimeError("unknown"), "send_unknown"),
    )
    for error, code in cases:
        with pytest.raises(DeliveryFailure) as raised:
            _run_gateway(monkeypatch, send_error=error)
        assert raised.value.code == code


@pytest.mark.parametrize("error", [ConnectionError("drop"), OSError("io"), TimeoutError("late")])
def test_telethon_bot_gateway_classifies_raw_transport_as_uncertain(error):
    def send_message(_telegram_user_id, _message):
        raise error

    gateway = TelethonBotGateway(send_message)
    with pytest.raises(DeliveryUncertain) as raised:
        gateway.push_private_message(1001, IncomingPrivateMessage(1, 88, 1, MediaKind.TEXT, "hi", None, 0))
    assert raised.value.code == "transport_unknown"


def test_telethon_bot_gateway_classifies_raw_definitive_failures():
    from telethon.errors import PeerIdInvalidError

    for error, code in ((PeerIdInvalidError(None), "peer_invalid"), (RuntimeError("bad"), "bot_push_failed")):
        def send_message(_telegram_user_id, _message, failure=error):
            raise failure

        with pytest.raises(DeliveryFailure) as raised:
            TelethonBotGateway(send_message).push_private_message(
                1001, IncomingPrivateMessage(1, 88, 1, MediaKind.TEXT, "hi", None, 0)
            )
        assert raised.value.code == code
    with pytest.raises(DeliveryFailure) as raised:
        TelethonBotGateway().push_private_message(
            1001, IncomingPrivateMessage(1, 88, 1, MediaKind.TEXT, "hi", None, 0)
        )
    assert raised.value.code == "bot_not_connected"


def test_disconnect_failure_after_send_success_keeps_outgoing_sent(factory, monkeypatch, caplog):
    _account_id, bot_message_id = _seed_mapping(factory)
    pool = TelethonSenderPool(
        lambda *_args: _run_gateway(monkeypatch, disconnect_error=OSError("disconnect failed"))
    )

    with caplog.at_level("ERROR"):
        with UnitOfWork(factory) as uow:
            result = PrivateRelayService(uow, RecordingBot(), pool).handle_bot_reply(_reply(bot_message_id))

    with UnitOfWork(factory) as uow:
        assert result.sent_message_id == 77
        assert uow.outgoing.get_by_reply(1, 700).status == "sent"
    assert "disconnect failed" in caplog.text


def test_disconnect_failure_after_send_failure_keeps_outgoing_failed(factory, monkeypatch, caplog):
    from telethon.errors import PeerIdInvalidError

    _account_id, bot_message_id = _seed_mapping(factory)
    pool = TelethonSenderPool(
        lambda *_args: _run_gateway(
            monkeypatch,
            send_error=PeerIdInvalidError(None),
            disconnect_error=OSError("disconnect failed"),
        )
    )

    with caplog.at_level("ERROR"):
        with pytest.raises(DeliveryFailure) as raised:
            with UnitOfWork(factory) as uow:
                PrivateRelayService(uow, RecordingBot(), pool).handle_bot_reply(_reply(bot_message_id))

    with UnitOfWork(factory) as uow:
        assert raised.value.code == "peer_invalid"
        assert uow.outgoing.get_by_reply(1, 700).status == "failed"
    assert "disconnect failed" in caplog.text


def test_no_failed_exception_leaks_uncommitted_outgoing(factory):
    _account_id, bot_message_id = _seed_mapping(factory)
    with pytest.raises(DeliveryFailure):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(
                uow,
                RecordingBot(),
                RecordingSender({DeveloperSlot.PRIMARY: DeliveryFailure("peer_invalid", "bad")}),
            ).handle_bot_reply(_reply(bot_message_id))
    with UnitOfWork(factory) as uow:
        assert uow.session.query(OutgoingReplyModel).one().status == "failed"


@pytest.mark.parametrize(
    "error",
    [
        DeliveryFailure("peer_invalid", "bad"),
        DeliveryUncertain("transport_unknown", "unknown"),
        SessionFailure("session failed"),
    ],
)
def test_gateway_boundaries_reraise_domain_errors_without_self_cause(monkeypatch, error):
    def fail(*_args):
        raise error

    message = IncomingPrivateMessage(1, 88, 1, MediaKind.TEXT, "hi", None, 0)
    slot = SessionSlotRef(1, 1, DeveloperSlot.PRIMARY, "encrypted")
    reply = OutgoingReply(1001, 700, 500, MediaKind.TEXT, "reply")
    boundaries = (
        lambda: TelethonBotGateway(fail).push_private_message(1001, message),
        lambda: TelethonSenderPool(fail).send_reply(slot, TelegramPeer(88), reply),
        lambda: _run_gateway(monkeypatch, send_error=error),
    )
    for invoke in boundaries:
        with pytest.raises(type(error)) as raised:
            invoke()
        assert raised.value is error
        assert raised.value.__cause__ is None


@pytest.mark.skipif(
    not os.getenv("TG_V_CHAT_TEST_DATABASE_URL"),
    reason="TG_V_CHAT_TEST_DATABASE_URL 未设置：并发 reply claim/逻辑 sender 为 unproven",
)
def test_postgresql_concurrent_same_reply_has_one_logical_sender():
    factory = create_session_factory(os.environ["TG_V_CHAT_TEST_DATABASE_URL"])
    init_db(factory)
    telegram_user_id = uuid4().int % 9_000_000_000 + 1
    _account_id, bot_message_id = _seed_mapping(factory, telegram_user_id)
    sender = RecordingSender()
    reply = OutgoingReply(telegram_user_id, 700, bot_message_id, MediaKind.TEXT, "reply")

    def deliver():
        with UnitOfWork(factory) as uow:
            return PrivateRelayService(uow, RecordingBot(), sender).handle_bot_reply(reply)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: deliver(), range(2)))

    assert sender.calls == [DeveloperSlot.PRIMARY]
    assert sorted(result.duplicate for result in results) == [False, True]
