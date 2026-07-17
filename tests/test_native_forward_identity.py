import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeliveryFailure, DeveloperSlot
from tg_v_chat.services.auth import AuthChallenge, AuthenticatedSession, AuthService
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.private_listener.process import BoundListenerSession, _sync_bound_account_identity


@pytest.fixture()
def session_factory():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    return factory


def test_account_telegram_identity_is_unique(session_factory):
    with UnitOfWork(session_factory) as uow:
        first_user = uow.users.get_or_create(1001)
        second_user = uow.users.get_or_create(1002)
        first = uow.accounts.create(first_user.id, "+15550000001")
        second = uow.accounts.create(second_user.id, "+15550000002")
        uow.accounts.update_telegram_identity(first.id, 7001)
        uow.commit()

    with UnitOfWork(session_factory) as uow:
        with pytest.raises(IntegrityError):
            uow.accounts.update_telegram_identity(second.id, 7001)


def test_listener_sync_persists_real_telegram_identity(session_factory):
    class Client:
        async def get_me(self):
            return SimpleNamespace(id=7001, first_name="接收", last_name="账号", username="receiver_user")

    with UnitOfWork(session_factory) as uow:
        user = uow.users.get_or_create(42)
        account = uow.accounts.create(user.id, "+19525920433")
        uow.commit()

    binding = BoundListenerSession(
        account_id=account.id,
        system_user_id=user.id,
        phone_number=account.phone_number,
        display_name=None,
        username=None,
        developer_slot="primary",
        session_string="session",
    )

    updated = asyncio.run(_sync_bound_account_identity(Client(), binding, session_factory))

    assert updated.telegram_user_id == 7001
    with UnitOfWork(session_factory) as uow:
        stored = uow.accounts.get(account.id)
        assert stored.telegram_user_id == 7001
        assert stored.display_name == "接收 账号"


def test_listener_sync_does_not_reclaim_identity_after_account_delete(session_factory):
    class Client:
        async def get_me(self):
            return SimpleNamespace(id=7001, first_name="接收", last_name="账号", username="receiver_user")

    with UnitOfWork(session_factory) as uow:
        user = uow.users.get_or_create(42)
        account = uow.accounts.create(user.id, "+19525920433")
        uow.accounts.update_telegram_identity(account.id, 7001)
        uow.accounts.mark_deleted(account.id)
        uow.commit()

    binding = BoundListenerSession(
        account_id=account.id,
        system_user_id=user.id,
        phone_number=account.phone_number,
        display_name=None,
        username=None,
        developer_slot="primary",
        session_string="session",
    )

    with pytest.raises(DeliveryFailure, match="account_unavailable"):
        asyncio.run(_sync_bound_account_identity(Client(), binding, session_factory))

    with UnitOfWork(session_factory) as uow:
        assert uow.accounts.get(account.id).telegram_user_id is None


def test_completed_authorization_persists_authenticated_telegram_identity(session_factory):
    with UnitOfWork(session_factory) as uow:
        service = AuthService(uow, _AuthenticatorWithIdentity(), SessionCipher("test-key"))
        challenge = service.start_binding(1001, "+15550000001", DeveloperSlot.PRIMARY)

        service.submit_code(challenge.id, "123456")

        account = uow.accounts.list_for_user(uow.users.get_by_telegram_id(1001).id)[0]
        assert account.status == "active"
        assert account.telegram_user_id == 7001


class _AuthenticatorWithIdentity:
    def start(self, phone_number, slot):
        return AuthChallenge(phone_number, slot, "code-hash", pending_session="partial-session")

    def complete_code(self, _challenge, _code):
        return AuthenticatedSession("session-string", 7001, "接收账号", "receiver")

    def complete_password(self, _challenge, _password):
        raise AssertionError("2FA is not expected")
