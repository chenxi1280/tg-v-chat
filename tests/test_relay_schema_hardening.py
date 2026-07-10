from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect
from sqlalchemy.exc import IntegrityError

from tg_v_chat.domain import (
    DeliveryFailure,
    DeliveryUncertain,
    DeveloperSlot,
    IncomingPrivateBatch,
    IncomingPrivateMessage,
    MediaArtifact,
    MediaKind,
    OutgoingReply,
    SessionStatus,
)
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.models import (
    AuthChallengeModel,
    BotPushMessageModel,
    OutgoingReplyModel,
    RelayMediaArtifactModel,
    RelayMediaGroupModel,
    ReplyMappingModel,
    SessionFailoverEventModel,
    TgSessionSlotModel,
)
from tg_v_chat.storage.repositories import UnitOfWork


DISPATCH_STATUSES = {"pending", "sending", "sent", "failed", "uncertain"}


@pytest.fixture()
def session_factory():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    return factory


def _create_relay(uow):
    user = uow.users.get_or_create(1001)
    account = uow.accounts.create(user.id, "+15550000001")
    relay, _ = uow.relays.create_or_get(
        IncomingPrivateMessage(account.id, 88, 101, MediaKind.TEXT, "hi", None, 0)
    )
    return user, account, relay


def test_domain_media_batch_and_delivery_results_are_immutable_and_compatible():
    artifact = MediaArtifact("abc", "a.webp", "image/webp", 4, MediaKind.STICKER, 1)
    message = IncomingPrivateMessage(1, 88, 101, MediaKind.TEXT, "hi", None, 0)
    reply = OutgoingReply(1, 500, 400, MediaKind.TEXT, "ok")

    assert artifact.metadata_json is None
    assert message.artifacts == ()
    assert reply.artifacts == ()
    assert IncomingPrivateBatch((message,)).messages == (message,)
    assert DeliveryFailure("peer_invalid", "peer unavailable").code == "peer_invalid"
    assert DeliveryUncertain("transport_unknown", "result unknown").detail == "result unknown"
    with pytest.raises(FrozenInstanceError):
        artifact.sequence = 2


def test_models_expose_runtime_lifecycle_columns_and_foreign_keys():
    assert _column_names(ReplyMappingModel) >= {"created_at", "invalidated_at"}
    assert _column_names(BotPushMessageModel) >= _dispatch_columns() | {"bot_message_id"}
    assert _column_names(OutgoingReplyModel) >= _dispatch_columns() | {
        "sent_message_id",
        "developer_slot",
    }
    assert _column_names(TgSessionSlotModel) >= {
        "failure_code",
        "last_checked_at",
        "last_healthy_at",
        "updated_at",
        "revision",
    }
    assert "purpose" in _column_names(AuthChallengeModel)
    assert SessionFailoverEventModel.__table__.c.to_slot.nullable
    assert _foreign_key_targets(ReplyMappingModel) >= {"bound_tg_accounts.id"}
    assert _foreign_key_targets(OutgoingReplyModel) >= {"relay_messages.id"}
    assert _foreign_key_targets(SessionFailoverEventModel) >= {"bound_tg_accounts.id"}
    assert "ck_reply_mapping_status" in _check_constraint_names(ReplyMappingModel)


def test_dispatch_models_have_five_state_defaults_and_unique_business_keys():
    assert BotPushMessageModel.__table__.c.status.default.arg == "pending"
    assert OutgoingReplyModel.__table__.c.status.default.arg == "pending"
    assert BotPushMessageModel.__table__.c.bot_message_id.nullable
    assert OutgoingReplyModel.__table__.c.sent_message_id.nullable
    assert OutgoingReplyModel.__table__.c.developer_slot.nullable
    assert _unique_column_sets(BotPushMessageModel) >= {("dispatch_key",), ("relay_message_id",)}
    assert _unique_column_sets(OutgoingReplyModel) >= {("dispatch_key",)}
    assert DISPATCH_STATUSES == set(BotPushMessageModel.DISPATCH_STATUSES)
    assert DISPATCH_STATUSES == set(OutgoingReplyModel.DISPATCH_STATUSES)


def test_media_models_define_artifact_and_group_contracts():
    assert _column_names(RelayMediaArtifactModel) >= {
        "relay_message_id",
        "outgoing_reply_id",
        "direction",
        "storage_key",
        "file_name",
        "mime_type",
        "byte_size",
        "media_kind",
        "sequence",
        "metadata_json",
        "status",
        "failure_reason",
        "created_at",
        "released_at",
    }
    assert _foreign_key_targets(RelayMediaArtifactModel) >= {
        "relay_messages.id",
        "outgoing_replies.id",
    }
    assert _unique_column_sets(RelayMediaArtifactModel) >= {("storage_key",)}
    assert RelayMediaArtifactModel.__table__.c.status.default.arg == "staging"
    assert set(RelayMediaArtifactModel.ARTIFACT_STATUSES) == {
        "staging",
        "ready",
        "sent",
        "failed",
        "released",
    }
    assert "ck_relay_media_artifact_status" in _check_constraint_names(RelayMediaArtifactModel)
    assert _column_names(RelayMediaGroupModel) >= {
        "bound_tg_account_id",
        "media_group_id",
        "item_count",
        "status",
        "dispatch_key",
        "failure_code",
        "failure_reason",
        "created_at",
        "updated_at",
    }
    assert _unique_column_sets(RelayMediaGroupModel) >= {
        ("dispatch_key",),
        ("bound_tg_account_id", "media_group_id"),
    }


def test_dispatch_repositories_create_claim_and_reach_terminal_states(session_factory):
    with UnitOfWork(session_factory) as uow:
        user, _account, relay = _create_relay(uow)
        push = uow.pushes.create_pending(relay.id, user.id, "push:101")
        outgoing = uow.outgoing.create_pending(
            700,
            system_user_id=user.id,
            relay_id=relay.id,
            dispatch_key="reply:700",
        )

        assert push.status == outgoing.status == "pending"
        assert uow.pushes.claim("push:101")
        assert not uow.pushes.claim("push:101")
        assert uow.outgoing.claim("reply:700")
        uow.pushes.mark_sent("push:101", bot_message_id=500)
        uow.outgoing.mark_uncertain("reply:700", "transport_unknown", "result unknown")

        assert uow.pushes.get_by_dispatch_key("push:101").status == "sent"
        stored = uow.outgoing.get_by_dispatch_key("reply:700")
        assert stored.status == "uncertain"
        assert stored.failure_code == "transport_unknown"

        with pytest.raises(ValueError, match="状态不可终结"):
            uow.pushes.mark_failed("push:101", "late_failure", "must not overwrite")
        assert uow.pushes.get_by_dispatch_key("push:101").status == "sent"


def test_media_repositories_create_claim_read_and_transition(session_factory):
    with UnitOfWork(session_factory) as uow:
        _user, account, relay = _create_relay(uow)
        artifact = uow.media_artifacts.create(
            direction="incoming",
            storage_key="artifact-1",
            file_name="a.webp",
            mime_type="image/webp",
            byte_size=4,
            media_kind=MediaKind.STICKER,
            sequence=1,
            relay_message_id=relay.id,
        )
        group = uow.media_groups.create(
            account.id,
            media_group_id="album-1",
            item_count=2,
            dispatch_key="album:1",
        )

        assert uow.media_artifacts.get_by_storage_key("artifact-1").id == artifact.id
        assert artifact.status == "staging"
        assert artifact.media_kind == MediaKind.STICKER.value
        assert artifact.sequence == 1
        with pytest.raises(ValueError, match="状态不可转换"):
            uow.media_artifacts.mark_sent("artifact-1")
        uow.media_artifacts.mark_ready("artifact-1")
        uow.media_artifacts.mark_sent("artifact-1")
        assert uow.media_groups.claim("album:1")
        uow.media_groups.mark_failed("album:1", "download_failed", "second item")
        uow.media_artifacts.mark_released("artifact-1")

        assert uow.media_groups.get_by_dispatch_key("album:1").status == "failed"
        assert uow.media_artifacts.get_by_storage_key("artifact-1").released_at is not None
        assert group.bound_tg_account_id == account.id
        with pytest.raises(ValueError, match="状态不可终结"):
            uow.media_groups.mark_uncertain("album:1", "late", "must not overwrite")
        assert group.status == "failed"


def test_failed_artifact_keeps_failure_evidence_after_release(session_factory):
    with UnitOfWork(session_factory) as uow:
        _user, _account, relay = _create_relay(uow)
        artifact = uow.media_artifacts.create(
            direction="incoming",
            storage_key="artifact-failed",
            file_name="b.webp",
            mime_type="image/webp",
            byte_size=5,
            media_kind=MediaKind.STICKER,
            sequence=2,
            relay_message_id=relay.id,
        )

        uow.media_artifacts.mark_failed("artifact-failed", "download_failed")
        uow.media_artifacts.mark_released("artifact-failed")

        assert artifact.status == "released"
        assert artifact.failure_reason == "download_failed"


def test_artifact_and_mapping_check_constraints_reject_invalid_states(session_factory):
    with UnitOfWork(session_factory) as uow:
        user, account, relay = _create_relay(uow)
        mapping = uow.mappings.create(500, relay, user.id)
        mapping.status = "unknown"
        with pytest.raises(IntegrityError):
            uow.session.flush()

    with UnitOfWork(session_factory) as uow:
        _user, _account, relay = _create_relay(uow)
        invalid = RelayMediaArtifactModel(
            relay_message_id=relay.id,
            direction="incoming",
            storage_key="invalid-artifact",
            file_name="a.webp",
            mime_type="image/webp",
            byte_size=4,
            media_kind=MediaKind.STICKER.value,
            sequence=1,
            status="pending",
        )
        uow.session.add(invalid)
        with pytest.raises(IntegrityError):
            uow.session.flush()


def test_mapping_and_artifact_migration_contracts_are_declared():
    migration = (
        __import__("pathlib").Path(__file__).parents[1]
        / "migrations/versions/0009_relay_runtime_hardening.py"
    ).read_text()

    assert "ck_reply_mapping_status" in migration
    assert "ck_relay_media_artifact_status" in migration
    assert 'sa.Column("media_kind"' in migration
    assert 'sa.Column("sequence"' in migration


def test_downgrade_offline_sql_rejects_expired_reply_mappings():
    env = {**os.environ, "TG_V_CHAT_DATABASE_URL": "postgresql+psycopg://user:pass@localhost/db"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "downgrade",
            "0009_relay_runtime_hardening:0008_account_display_identity",
            "--sql",
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "reply_mappings WHERE status = 'expired'" in result.stdout
    assert "0009 downgrade: expired ReplyMapping would be reactivated" in result.stdout


def test_mapping_invalidation_and_targeted_slot_authorization(session_factory):
    with UnitOfWork(session_factory) as uow:
        user, account, relay = _create_relay(uow)
        mapping = uow.mappings.create(500, relay, user.id)
        primary = uow.sessions.authorize_or_replace(
            account.id,
            DeveloperSlot.PRIMARY,
            encrypted_session="primary-v1",
            status=SessionStatus.ACTIVE,
        )
        standby = uow.sessions.authorize_or_replace(
            account.id,
            DeveloperSlot.STANDBY_1,
            encrypted_session="standby-v1",
            status=SessionStatus.STANDBY,
        )
        updated = uow.sessions.authorize_or_replace(
            account.id,
            DeveloperSlot.PRIMARY,
            encrypted_session="primary-v2",
            status=SessionStatus.ACTIVE,
        )

        assert primary.id == updated.id
        assert updated.revision == 2
        assert uow.sessions.get_for_account(account.id, DeveloperSlot.STANDBY_1).id == standby.id
        assert uow.mappings.invalidate_for_account(account.id) == 1
        assert mapping.status == "expired"
        assert isinstance(mapping.created_at, datetime)
        assert isinstance(mapping.invalidated_at, datetime)


def test_auth_challenge_purpose_and_sqlite_account_lock_are_exposed(session_factory):
    with UnitOfWork(session_factory) as uow:
        _user, account, _relay = _create_relay(uow)
        challenge = uow.auth_challenges.create(
            account.id,
            phone=account.phone_number,
            slot=DeveloperSlot.PRIMARY,
            code_hash="code",
        )
        with uow.account_locks.acquire(account.id):
            uow.commit()

        assert challenge.purpose == "initial_bind"


def test_session_authorization_rejects_slot_status_mismatch_without_mutation(session_factory):
    with UnitOfWork(session_factory) as uow:
        _user, account, _relay = _create_relay(uow)
        primary = uow.sessions.authorize_or_replace(
            account.id,
            DeveloperSlot.PRIMARY,
            encrypted_session="primary-v1",
            status=SessionStatus.ACTIVE,
        )
        uow.sessions.mark_failed(primary.id, "expired", code="auth_expired")

        for status in (SessionStatus.STANDBY, SessionStatus.FAILED, SessionStatus.EXPIRED):
            with pytest.raises(ValueError, match="slot 状态不匹配"):
                uow.sessions.authorize_or_replace(
                    account.id,
                    DeveloperSlot.PRIMARY,
                    encrypted_session="must-not-replace",
                    status=status,
                )
        with pytest.raises(ValueError, match="slot 状态不匹配"):
            uow.sessions.authorize_or_replace(
                account.id,
                DeveloperSlot.STANDBY_1,
                encrypted_session="must-not-create",
                status=SessionStatus.ACTIVE,
            )
        with pytest.raises(ValueError, match="slot 状态不匹配"):
            uow.sessions.mark_healthy(primary.id, SessionStatus.STANDBY)

        assert primary.encrypted_session == "primary-v1"
        assert primary.status == SessionStatus.FAILED.value
        assert primary.failure_code == "auth_expired"
        assert primary.failure_reason == "expired"
        assert primary.revision == 1
        assert uow.sessions.get_for_account(account.id, DeveloperSlot.STANDBY_1) is None


def test_unit_of_work_closes_session_when_rollback_raises():
    class RaisingSession:
        closed = False

        def rollback(self):
            raise RuntimeError("rollback failed")

        def close(self):
            self.closed = True

    class FakeFactory:
        kw = {"bind": object()}

        def __init__(self):
            self.session = RaisingSession()

        def __call__(self):
            return self.session

    factory = FakeFactory()
    uow = UnitOfWork(factory)
    uow.__enter__()

    with pytest.raises(RuntimeError, match="rollback failed"):
        uow.__exit__(RuntimeError, RuntimeError("service failed"), None)

    assert factory.session.closed


def _column_names(model) -> set[str]:
    return set(model.__table__.c.keys())


def _dispatch_columns() -> set[str]:
    return {"dispatch_key", "failure_code", "failure_reason", "created_at", "updated_at"}


def _foreign_key_targets(model) -> set[str]:
    return {foreign_key.target_fullname for foreign_key in model.__table__.foreign_keys}


def _unique_column_sets(model) -> set[tuple[str, ...]]:
    inspector = inspect(model.__table__)
    values = {
        tuple(constraint.columns.keys())
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    values.update((column.name,) for column in inspector.columns if column.unique)
    return values


def _check_constraint_names(model) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
