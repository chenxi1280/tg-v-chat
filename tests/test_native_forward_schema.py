from datetime import datetime, timedelta, timezone

from tg_v_chat.domain import IncomingPrivateMessage, MediaKind, TelegramPeer
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.models import (
    NativeForwardBatchModel,
    NativeForwardBridgeQuarantineModel,
    NativeForwardItemModel,
)
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.storage.repositories.native_forward import NativeForwardRepository


def _now() -> datetime:
    return datetime(2026, 7, 17, tzinfo=timezone.utc)


def _factory():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    return factory


def _batch_with_two_relays(uow):
    user = uow.users.get_or_create(1001)
    account = uow.accounts.create(user.id, "+15550000001")
    uow.accounts.mark_active(account.id)
    uow.accounts.update_telegram_identity(account.id, 7001)
    first, _ = uow.relays.create_or_get(
        IncomingPrivateMessage(account.id, 88, 101, MediaKind.PHOTO, "one", "album", 9)
    )
    second, _ = uow.relays.create_or_get(
        IncomingPrivateMessage(account.id, 88, 102, MediaKind.VIDEO, "two", "album", 3)
    )
    batch = uow.native_forwards.create_collecting(
        system_user_id=user.id,
        account_id=account.id,
        telegram_user_id=7001,
        peer=TelegramPeer(88, 9901),
        collect_until=_now() + timedelta(seconds=1),
        marker_token="marker-1",
    )
    return user, account, batch, first, second


def test_native_forward_models_keep_batch_order_and_private_quarantine_boundary():
    assert _column_names(NativeForwardBatchModel) >= {
        "bridge_sender_telegram_user_id",
        "bridge_deadline_at",
        "header_bot_message_id",
        "marker_token",
        "expected_count",
    }
    assert _column_names(NativeForwardItemModel) >= {
        "batch_sequence",
        "bridge_sender_telegram_user_id",
        "bridge_message_id",
        "bot_push_message_id",
        "final_bot_message_id",
    }
    assert _column_names(NativeForwardBridgeQuarantineModel) == {
        "id",
        "sender_telegram_user_id",
        "bot_message_id",
        "marker_token",
        "failure_code",
        "created_at",
    }


def test_native_forward_v2_does_not_keep_sender_side_message_id_ledger():
    assert "expected_bridge_message_id" not in _column_names(NativeForwardItemModel)
    assert not hasattr(NativeForwardRepository, "record_first_hop_result")


def test_append_assigns_batch_sequence_not_relay_media_sequence():
    factory = _factory()
    with UnitOfWork(factory) as uow:
        _user, _account, batch, first, second = _batch_with_two_relays(uow)

        first_item = uow.native_forwards.append_item(batch.id, first.id)
        second_item = uow.native_forwards.append_item(batch.id, second.id)

        assert (first_item.batch_sequence, second_item.batch_sequence) == (1, 2)
        assert (first.sequence, second.sequence) == (9, 3)


def test_bridge_message_id_is_scoped_to_the_bound_sender_private_chat():
    factory = _factory()
    with UnitOfWork(factory) as uow:
        first = _awaiting_single_item_batch(uow, user_id=1001, sender_id=7001, marker_token="marker-1")
        second = _awaiting_single_item_batch(uow, user_id=1002, sender_id=7002, marker_token="marker-2")

        uow.native_forwards.append_bridge_message(7001, 601, "linked")
        uow.native_forwards.append_bridge_message(7002, 601, "linked")

        assert uow.native_forwards.list_items(first.id)[0].bridge_message_id == 601
        assert uow.native_forwards.list_items(second.id)[0].bridge_message_id == 601


def test_final_pushes_are_created_and_claimed_before_final_forward():
    factory = _factory()
    with UnitOfWork(factory) as uow:
        _user, _account, batch, first, second = _batch_with_two_relays(uow)
        uow.native_forwards.append_item(batch.id, first.id)
        uow.native_forwards.append_item(batch.id, second.id)
        uow.native_forwards.seal(batch.id)
        uow.native_forwards.claim_bridge(batch.id, _now() + timedelta(seconds=30))
        uow.native_forwards.mark_awaiting_bot("marker-1", 7001, marker_message_id=900)
        uow.native_forwards.append_bridge_message(7001, 901, "linked")
        uow.native_forwards.append_bridge_message(7001, 902, "name_only")

        claimed = uow.native_forwards.claim_final("marker-1")
        pushes = uow.native_forwards.create_and_claim_final_pushes(claimed.id)

        assert claimed.status == "final_sending"
        assert [push.status for push in pushes] == ["sending", "sending"]
        assert [push.dispatch_key for push in pushes] == [f"push:{first.id}", f"push:{second.id}"]


def test_bot_marker_id_is_persisted_when_bot_consumes_the_marker():
    factory = _factory()
    with UnitOfWork(factory) as uow:
        _user, _account, batch, first, _second = _batch_with_two_relays(uow)
        uow.native_forwards.append_item(batch.id, first.id)
        uow.native_forwards.seal(batch.id)
        uow.native_forwards.claim_bridge(batch.id, _now() + timedelta(seconds=30))
        stored = uow.native_forwards.mark_awaiting_bot("marker-1", 7001, marker_message_id=900)

        assert stored.status == "awaiting_bot"
        assert stored.first_hop_marker_message_id == 900
        assert uow.native_forwards.list_items(batch.id)[0].bridge_message_id is None


def test_expired_final_sending_batch_becomes_uncertain_without_replay():
    factory = _factory()
    deadline = _now() + timedelta(seconds=30)
    with UnitOfWork(factory) as uow:
        _user, _account, batch, first, second = _batch_with_two_relays(uow)
        uow.native_forwards.append_item(batch.id, first.id)
        uow.native_forwards.append_item(batch.id, second.id)
        uow.native_forwards.seal(batch.id)
        uow.native_forwards.claim_bridge(batch.id, deadline)
        uow.native_forwards.mark_awaiting_bot("marker-1", 7001, marker_message_id=900)
        uow.native_forwards.append_bridge_message(7001, 901, "linked")
        uow.native_forwards.append_bridge_message(7001, 902, "linked")
        uow.native_forwards.claim_final("marker-1")
        uow.native_forwards.create_and_claim_final_pushes(batch.id)

        expired = uow.native_forwards.expire_bridge_batches(deadline + timedelta(seconds=1))

        assert [item.id for item in expired] == [batch.id]
        assert uow.native_forwards.get(batch.id).status == "uncertain"
        assert uow.native_forwards.get(batch.id).failure_code == "bridge_timeout"


def _column_names(model) -> set[str]:
    return {column.name for column in model.__table__.columns}


def _awaiting_single_item_batch(uow, *, user_id: int, sender_id: int, marker_token: str):
    user = uow.users.get_or_create(user_id)
    account = uow.accounts.create(user.id, f"+1555000{user_id}")
    uow.accounts.mark_active(account.id)
    uow.accounts.update_telegram_identity(account.id, sender_id)
    relay, _ = uow.relays.create_or_get(
        IncomingPrivateMessage(account.id, 88, 101, MediaKind.TEXT, "body", None, 1, 9901)
    )
    batch = uow.native_forwards.create_collecting(
        system_user_id=user.id,
        account_id=account.id,
        telegram_user_id=sender_id,
        peer=TelegramPeer(88, 9901),
        collect_until=_now(),
        marker_token=marker_token,
    )
    uow.native_forwards.append_item(batch.id, relay.id)
    uow.native_forwards.seal(batch.id)
    uow.native_forwards.claim_bridge(batch.id, _now() + timedelta(seconds=30))
    uow.native_forwards.mark_awaiting_bot(marker_token, sender_id, marker_message_id=500)
    return batch
