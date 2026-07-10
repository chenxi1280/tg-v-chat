from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, current_thread

import pytest

from tg_v_chat.bot.account_management import AccountManagementService
from tg_v_chat.domain import IncomingPrivateMessage, MediaKind
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.storage.repositories.accounts import AccountRepository


TELEGRAM_USER_ID = 1001


class BlockingBot:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.calls = 0
        self._guard = Lock()

    def push_private_message(self, _telegram_user_id, _message):
        with self._guard:
            self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release Bot push")
        return 501

    def notify_failure(self, _telegram_user_id, _text):
        raise AssertionError("unexpected failure notification")


class UnusedSender:
    def send_reply(self, _session_slot, _peer, _reply):
        raise AssertionError("unexpected outbound send")


@pytest.fixture()
def factory(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'relay-concurrency.db'}")
    init_db(session_factory)
    return session_factory


def _seed_account(factory):
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(TELEGRAM_USER_ID)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.accounts.mark_active(account.id)
        uow.commit()
        return user.id, account.id


def _receive(factory, bot, message):
    with UnitOfWork(factory) as uow:
        return PrivateRelayService(uow, bot, UnusedSender()).receive_private_message(message)


def _create_mapping(factory, *, user_id: int, account_id: int):
    with UnitOfWork(factory) as uow:
        relay, _ = uow.relays.create_or_get(
            IncomingPrivateMessage(account_id, 88, 700, MediaKind.TEXT, "hi", None, 0)
        )
        uow.mappings.create(500, relay, user_id)
        uow.commit()


def test_live_duplicate_waits_for_first_push_and_does_not_become_uncertain(factory):
    user_id, account_id = _seed_account(factory)
    message = IncomingPrivateMessage(account_id, 88, 101, MediaKind.TEXT, "hi", None, 0)
    bot = BlockingBot()
    duplicate_done = Event()

    def receive_duplicate():
        try:
            return _receive(factory, bot, message)
        finally:
            duplicate_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_receive, factory, bot, message)
        assert bot.entered.wait(timeout=5)
        duplicate = executor.submit(receive_duplicate)
        try:
            assert not duplicate_done.wait(timeout=0.2)
        finally:
            bot.release.set()
        results = (first.result(timeout=5), duplicate.result(timeout=5))

    with UnitOfWork(factory) as uow:
        push = uow.pushes.get_by_relay(results[0].relay_message_id)
        mapping = uow.mappings.get_by_bot_message(user_id, 501)
        assert push.status == "sent"
        assert mapping.status == "active"
    assert bot.calls == 1
    assert sorted(result.duplicate for result in results) == [False, True]


def test_disable_waits_for_inflight_push_then_expires_mapping(factory):
    user_id, account_id = _seed_account(factory)
    message = IncomingPrivateMessage(account_id, 88, 102, MediaKind.TEXT, "hi", None, 0)
    bot = BlockingBot()
    disable_done = Event()
    manager = AccountManagementService(factory, object(), object())

    def disable_account():
        try:
            return manager.disable(TELEGRAM_USER_ID, account_id)
        finally:
            disable_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        incoming = executor.submit(_receive, factory, bot, message)
        assert bot.entered.wait(timeout=5)
        disabled = executor.submit(disable_account)
        try:
            assert not disable_done.wait(timeout=0.2)
        finally:
            bot.release.set()
        result = incoming.result(timeout=5)
        disabled.result(timeout=5)

    with UnitOfWork(factory) as uow:
        mapping = uow.mappings.get_by_bot_message(user_id, result.bot_message_id)
        assert uow.accounts.get(account_id).status == "disabled"
        assert uow.pushes.get_by_relay(result.relay_message_id).status == "sent"
        assert mapping.status == "expired"
        assert mapping.invalidated_at is not None


@pytest.mark.parametrize(
    ("waiting_action", "leading_action"),
    [("disable", "delete"), ("delete", "disable")],
)
def test_concurrent_disable_delete_keeps_deleted_terminal(
    factory, monkeypatch, waiting_action, leading_action
):
    user_id, account_id = _seed_account(factory)
    _create_mapping(factory, user_id=user_id, account_id=account_id)
    loaded_before_lock = Event()
    thread_prefix = f"{waiting_action}-waiter"
    original_get = AccountRepository.get_for_user

    def tracked_get(repository, target_account_id, target_user_id):
        account = original_get(repository, target_account_id, target_user_id)
        if current_thread().name.startswith(thread_prefix):
            loaded_before_lock.set()
        return account

    monkeypatch.setattr(AccountRepository, "get_for_user", tracked_get)
    waiting_manager = AccountManagementService(factory, object(), object())
    leading_manager = AccountManagementService(factory, object(), object())
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=thread_prefix)
    try:
        with UnitOfWork(factory) as blocker:
            with blocker.account_locks.acquire(account_id):
                waiting = executor.submit(
                    getattr(waiting_manager, waiting_action), TELEGRAM_USER_ID, account_id
                )
                assert loaded_before_lock.wait(timeout=5)
                getattr(leading_manager, leading_action)(TELEGRAM_USER_ID, account_id)
        waiting.result(timeout=5)
    finally:
        executor.shutdown(wait=True)

    with UnitOfWork(factory) as uow:
        account = uow.accounts.get(account_id)
        mapping = uow.mappings.get_by_bot_message(user_id, 500)
        assert account.status == "deleted"
        assert uow.accounts.list_for_user(user_id) == []
        assert mapping.status == "expired"
        assert mapping.invalidated_at is not None
