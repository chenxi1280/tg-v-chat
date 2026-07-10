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


def _binding(account_id, slot_id=1, revision=1):
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
