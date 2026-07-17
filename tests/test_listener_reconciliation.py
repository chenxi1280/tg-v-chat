import asyncio

import pytest

from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeveloperSlot, SessionStatus
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.private_listener.process import (
    BoundListenerSession,
    ListenerClientState,
    TelethonPrivateListenerProcess,
    _load_active_bindings,
    _require_v2_identity_readiness,
)


def _process(monkeypatch, bindings):
    import tg_v_chat.telegram.private_listener.process as process_module

    monkeypatch.setattr(process_module, "_load_active_bindings", lambda *_args: bindings)
    return TelethonPrivateListenerProcess({}, "bot", object(), session_cipher=object())


class Client:
    def __init__(self, name):
        self.name = name
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


def _binding(account_id, slot_id=1, revision=1, telegram_user_id=None):
    return BoundListenerSession(
        account_id,
        42,
        f"+1555000000{account_id}",
        None,
        None,
        "primary",
        "session",
        slot_id=slot_id,
        session_revision=revision,
        telegram_user_id=telegram_user_id,
    )


def test_listener_start_failure_isolated_per_account(monkeypatch):
    bindings = [_binding(1), _binding(2)]
    process = _process(monkeypatch, bindings)

    async def start(binding, _gateway):
        if binding.account_id == 1:
            raise RuntimeError("boom")
        return Client("healthy")

    process._start_user_client = start

    asyncio.run(process._sync_clients(object()))

    assert sorted(process._clients) == [2]


def test_v2_listener_refuses_operational_account_without_telegram_identity(monkeypatch):
    import tg_v_chat.telegram.private_listener.process as process_module

    process = _process(monkeypatch, [_binding(1)])
    process._native_forward_v2_enabled = True
    process._bot_username = "relay_bot"
    monkeypatch.setattr(process_module, "_require_v2_identity_readiness", lambda *_args: None)

    with pytest.raises(RuntimeError, match="bound_account_identity_missing"):
        asyncio.run(process._sync_clients(object()))


def test_v2_readiness_checks_operational_accounts_without_listener_session():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(42)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.accounts.mark_active(account.id)
        uow.commit()

    with pytest.raises(RuntimeError, match=f"bound_account_identity_missing: {account.id}"):
        _require_v2_identity_readiness(factory, "relay_bot")


def test_v2_listener_runs_periodic_native_recovery(monkeypatch):
    import tg_v_chat.telegram.private_listener.process as process_module

    binding = _binding(1, telegram_user_id=7001)
    process = _process(monkeypatch, [binding])
    process._native_forward_v2_enabled = True
    process._bot_username = "relay_bot"
    process._clients[1] = ListenerClientState(Client("connected"), binding.fingerprint)
    monkeypatch.setattr(process_module, "_require_v2_identity_readiness", lambda *_args: None)
    monkeypatch.setattr(process_module, "_require_v2_binding_identities", lambda *_args: None)
    calls = []

    async def recover(bindings, gateway):
        calls.append((bindings, gateway))

    process._recover_v2_batches = recover
    gateway = object()

    asyncio.run(process._sync_clients(gateway))

    assert calls == [([binding], gateway)]


def test_v2_recovery_reconciles_and_dispatches_connected_accounts(monkeypatch):
    import tg_v_chat.telegram.private_listener.process as process_module

    binding = _binding(1, telegram_user_id=7001)
    process = _process(monkeypatch, [binding])
    process._native_forward_v2_enabled = True
    process._bot_username = "relay_bot"
    process._clients[1] = ListenerClientState(Client("connected"), binding.fingerprint)
    calls = []
    gateway = object()
    monkeypatch.setattr(
        process_module,
        "_reconcile_native_forwards",
        lambda session_factory, notifier: calls.append(("reconcile", session_factory, notifier)),
    )
    monkeypatch.setattr(
        process_module,
        "_dispatch_native_due",
        lambda session_factory, account_id, forwarder, bridge_timeout_seconds, notifier: calls.append(
            ("dispatch", session_factory, account_id, bridge_timeout_seconds, notifier, forwarder)
        ),
    )

    asyncio.run(process._recover_v2_batches([binding], gateway))

    assert calls[0] == ("reconcile", process._session_factory, gateway)
    assert calls[1][0:5] == ("dispatch", process._session_factory, 1, 30, gateway)


def test_listener_replaces_client_when_binding_fingerprint_changes(monkeypatch):
    binding = _binding(1, slot_id=2, revision=3)
    process = _process(monkeypatch, [binding])
    old = Client("old")
    process._clients[1] = ListenerClientState(old, ("old",))

    async def start(_binding, _gateway):
        return Client("new")

    process._start_user_client = start

    asyncio.run(process._sync_clients(object()))

    assert old.disconnected is True
    assert process._clients[1].fingerprint == binding.fingerprint
    assert process._clients[1].client.name == "new"


def test_listener_loads_operational_standby_binding_for_degraded_account():
    cipher = SessionCipher("test-key")
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(42)
        account = uow.accounts.create(user.id, "+15550000001")
        primary = uow.sessions.create(
            account.id,
            slot=DeveloperSlot.PRIMARY,
            encrypted_session=cipher.encrypt("bad"),
            status=SessionStatus.ACTIVE,
        )
        standby = uow.sessions.create(
            account.id,
            slot=DeveloperSlot.STANDBY_1,
            encrypted_session=cipher.encrypt("standby"),
            status=SessionStatus.STANDBY,
        )
        uow.sessions.mark_failed(primary.id, "primary failed")
        uow.accounts.mark_degraded(account.id)
        uow.commit()

    bindings = _load_active_bindings(factory, cipher)

    assert len(bindings) == 1
    assert bindings[0].developer_slot == DeveloperSlot.STANDBY_1.value
    assert bindings[0].slot_id == standby.id
    assert bindings[0].session_string == "standby"


def test_private_album_event_builder_is_incoming_only():
    from tg_v_chat.telegram.private_listener.process import private_album_event_builder

    builder = private_album_event_builder()

    assert getattr(builder, "outgoing", False) is False
