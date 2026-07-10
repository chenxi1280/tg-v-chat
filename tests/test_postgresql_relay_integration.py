from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tg_v_chat.domain import IncomingPrivateMessage, MediaKind
from tg_v_chat.storage.database import create_session_factory
from tg_v_chat.storage.repositories import UnitOfWork


TEST_DATABASE_URL = os.getenv("TG_V_CHAT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TG_V_CHAT_TEST_DATABASE_URL 未设置：PostgreSQL 在线约束/claim/advisory lock 为 unproven",
)


@pytest.fixture(scope="module")
def pg_factory():
    with _migration_database_url(TEST_DATABASE_URL):
        command.upgrade(Config("alembic.ini"), "head")
    return create_session_factory(TEST_DATABASE_URL)


def test_postgresql_foreign_key_rejects_orphan(pg_factory):
    engine = pg_factory.kw["bind"]

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO session_failover_events
                    (bound_tg_account_id, from_slot, to_slot, status, reason, created_at)
                    VALUES (-999999, 'primary', NULL, 'exhausted', 'test', CURRENT_TIMESTAMP)"""
                )
            )


def test_postgresql_reply_mapping_status_constraint_rejects_invalid_value(pg_factory):
    with UnitOfWork(pg_factory) as uow:
        telegram_user_id = uuid4().int % 9_000_000_000 + 1
        user = uow.users.get_or_create(telegram_user_id)
        account = uow.accounts.create(user.id, "+15550000002")
        relay, _ = uow.relays.create_or_get(
            IncomingPrivateMessage(account.id, 89, 102, MediaKind.TEXT, "hi", None, 0)
        )
        uow.commit()

    engine = pg_factory.kw["bind"]
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO reply_mappings
                    (bot_message_id, system_user_id, relay_message_id, bound_tg_account_id,
                     peer_id, source_message_id, media_kind, status, created_at)
                    VALUES (:bot_id, :user_id, :relay_id, :account_id,
                            89, 102, 'text', 'unknown', CURRENT_TIMESTAMP)"""
                ),
                {
                    "bot_id": uuid4().int % 2_000_000_000 + 1,
                    "user_id": user.id,
                    "relay_id": relay.id,
                    "account_id": account.id,
                },
            )


def test_0009_orphan_preflight_fails_without_deleting_source_row():
    with _isolated_schema_url(TEST_DATABASE_URL) as isolated_url:
        with _migration_database_url(isolated_url):
            command.upgrade(Config("alembic.ini"), "0008_account_display_identity")
        isolated_engine = create_engine(isolated_url, future=True)
        try:
            with isolated_engine.begin() as connection:
                connection.execute(
                    text(
                        """INSERT INTO session_failover_events
                        (bound_tg_account_id, from_slot, to_slot, status, reason, created_at)
                        VALUES (-999999, 'primary', 'primary', 'exhausted', 'orphan', CURRENT_TIMESTAMP)"""
                    )
                )
            with _migration_database_url(isolated_url):
                with pytest.raises(Exception, match="orphan session_failover_events.bound_tg_account_id"):
                    command.upgrade(Config("alembic.ini"), "0009_relay_runtime_hardening")
            with isolated_engine.connect() as connection:
                remaining = connection.scalar(text("SELECT count(*) FROM session_failover_events"))
            assert remaining == 1
        finally:
            isolated_engine.dispose()


def test_postgresql_two_claimers_only_one_succeeds(pg_factory):
    dispatch_key = f"pg-claim:{uuid4()}"
    with UnitOfWork(pg_factory) as uow:
        telegram_user_id = uuid4().int % 9_000_000_000 + 1
        user = uow.users.get_or_create(telegram_user_id)
        account = uow.accounts.create(user.id, "+15550000001")
        relay, _ = uow.relays.create_or_get(
            IncomingPrivateMessage(account.id, 88, 101, MediaKind.TEXT, "hi", None, 0)
        )
        uow.pushes.create_pending(relay.id, user.id, dispatch_key)
        uow.commit()

    def claim() -> bool:
        with UnitOfWork(pg_factory) as uow:
            claimed = uow.pushes.claim(dispatch_key)
            uow.commit()
            return claimed

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: claim(), range(2)))

    assert sorted(results) == [False, True]


def test_postgresql_competing_terminal_transitions_only_one_succeeds(pg_factory):
    dispatch_key = f"pg-terminal:{uuid4()}"
    with UnitOfWork(pg_factory) as uow:
        telegram_user_id = uuid4().int % 9_000_000_000 + 1
        user = uow.users.get_or_create(telegram_user_id)
        account = uow.accounts.create(user.id, "+15550000003")
        relay, _ = uow.relays.create_or_get(
            IncomingPrivateMessage(account.id, 90, 103, MediaKind.TEXT, "hi", None, 0)
        )
        uow.pushes.create_pending(relay.id, user.id, dispatch_key)
        assert uow.pushes.claim(dispatch_key)
        uow.commit()

    barrier = Barrier(2)

    def pause_before_update(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("UPDATE bot_push_messages SET"):
            barrier.wait(timeout=5)

    def finish(terminal: str) -> str:
        try:
            with UnitOfWork(pg_factory) as uow:
                if terminal == "sent":
                    uow.pushes.mark_sent(dispatch_key, bot_message_id=501)
                else:
                    uow.pushes.mark_failed(dispatch_key, "send_failed", "failed")
                uow.commit()
            return terminal
        except ValueError:
            return "rejected"

    engine = pg_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", pause_before_update)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(finish, ("sent", "failed")))
    finally:
        event.remove(engine, "before_cursor_execute", pause_before_update)

    assert results.count("rejected") == 1
    assert len(set(results) & {"sent", "failed"}) == 1


def test_postgresql_advisory_lock_serializes_account_operations(pg_factory):
    account_id = uuid4().int % 2_000_000_000 + 1
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def hold_first():
        with UnitOfWork(pg_factory) as uow:
            with uow.account_locks.acquire(account_id):
                first_entered.set()
                assert release_first.wait(timeout=5)

    def enter_second():
        assert first_entered.wait(timeout=5)
        with UnitOfWork(pg_factory) as uow:
            with uow.account_locks.acquire(account_id):
                second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(hold_first)
        second = executor.submit(enter_second)
        assert first_entered.wait(timeout=5)
        assert not second_entered.wait(timeout=0.2)
        release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert second_entered.is_set()


def test_0009_downgrade_rejects_expired_mapping_without_losing_evidence():
    with _isolated_schema_url(TEST_DATABASE_URL) as isolated_url:
        with _migration_database_url(isolated_url):
            command.upgrade(Config("alembic.ini"), "0009_relay_runtime_hardening")
        isolated_engine = create_engine(isolated_url, future=True)
        try:
            _insert_expired_mapping(isolated_engine)
            with _migration_database_url(isolated_url):
                with pytest.raises(Exception, match="expired ReplyMapping would be reactivated"):
                    command.downgrade(Config("alembic.ini"), "0008_account_display_identity")
            with isolated_engine.connect() as connection:
                row = connection.execute(
                    text("SELECT status, invalidated_at FROM reply_mappings WHERE id = 1")
                ).one()
            assert row.status == "expired"
            assert row.invalidated_at is not None
        finally:
            isolated_engine.dispose()


@contextmanager
def _migration_database_url(database_url: str | None):
    previous = os.environ.get("TG_V_CHAT_DATABASE_URL")
    os.environ["TG_V_CHAT_DATABASE_URL"] = database_url or ""
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TG_V_CHAT_DATABASE_URL", None)
        else:
            os.environ["TG_V_CHAT_DATABASE_URL"] = previous


@contextmanager
def _isolated_schema_url(database_url: str | None):
    if database_url is None:
        yield ""
        return
    schema_name = f"tg_v_chat_0009_{uuid4().hex}"
    base_engine = create_engine(database_url, future=True)
    isolated_url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    try:
        with base_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        yield isolated_url.render_as_string(hide_password=False)
    finally:
        with base_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        base_engine.dispose()


def _insert_expired_mapping(engine) -> None:
    statements = (
        "INSERT INTO system_users (id, telegram_user_id, status, created_at) VALUES (1, 1001, 'active', CURRENT_TIMESTAMP)",
        "INSERT INTO bound_tg_accounts (id, system_user_id, phone_number, status, created_at) VALUES (1, 1, '+1', 'disabled', CURRENT_TIMESTAMP)",
        """INSERT INTO relay_messages
        (id, bound_tg_account_id, peer_id, source_message_id, media_kind, payload, sequence, status)
        VALUES (1, 1, 88, 101, 'text', 'hi', 0, 'received')""",
        """INSERT INTO reply_mappings
        (id, bot_message_id, system_user_id, relay_message_id, bound_tg_account_id, peer_id,
         source_message_id, media_kind, status, created_at, invalidated_at)
        VALUES (1, 500, 1, 1, 1, 88, 101, 'text', 'expired', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
