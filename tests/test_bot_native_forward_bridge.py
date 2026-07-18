import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tg_v_chat.domain import IncomingPrivateMessage, MediaKind, TelegramPeer
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.models import NativeForwardBridgeQuarantineModel
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.private_listener.native_forward import marker_text
from tg_v_chat.telegram.telethon_clients.bot_process import TelethonBotProcess
from tg_v_chat.telegram.telethon_clients.config import DeveloperAppConfig
from tg_v_chat.telegram.telethon_clients.forward_bridge import NativeForwardBridgeHandler


class BotClient:
    def __init__(self, final_ids=(801, 802)):
        self.final_ids = list(final_ids)
        self.calls = []

    async def send_message(self, target, text):
        self.calls.append(("header", target, text))
        return SimpleNamespace(id=700)

    async def forward_messages(self, target, message_ids, *, from_peer):
        self.calls.append(("forward", target, tuple(message_ids), from_peer))
        return [SimpleNamespace(id=message_id) for message_id in self.final_ids]


class HeaderFailsOnceClient(BotClient):
    def __init__(self):
        super().__init__()
        self._failed = False

    async def send_message(self, target, text):
        if not self._failed:
            self._failed = True
            raise ConnectionError("header result unknown")
        self.calls.append(("failure", target, text))
        return SimpleNamespace(id=701)


def test_bot_process_stops_generic_router_when_bridge_consumes_update():
    from telethon import events

    class ConsumingBridge:
        async def handle_message(self, event):
            assert event.is_private is True
            return True

    process = TelethonBotProcess(
        DeveloperAppConfig(1, "hash"),
        "token",
        object(),
        bridge_handler=ConsumingBridge(),
    )

    with pytest.raises(events.StopPropagation):
        asyncio.run(process._handle_bridge_new_message(SimpleNamespace(is_private=True)))


def _factory():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    return factory


def _prepared_batch(factory, *, marker="marker-token"):
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(1001)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.accounts.mark_active(account.id)
        uow.accounts.update_telegram_identity(account.id, 7001)
        relays = []
        for source_id in (11, 12):
            relay, _ = uow.relays.create_or_get(
                IncomingPrivateMessage(account.id, 88, source_id, MediaKind.TEXT, "body", None, 1, 9901)
            )
            relays.append(relay)
        batch = uow.native_forwards.create_collecting(
            system_user_id=user.id,
            account_id=account.id,
            telegram_user_id=7001,
            peer=TelegramPeer(88, 9901),
            collect_until=now,
            marker_token=marker,
        )
        for relay in relays:
            uow.native_forwards.append_item(batch.id, relay.id)
        uow.native_forwards.seal(batch.id)
        uow.native_forwards.claim_bridge(batch.id, now + timedelta(seconds=30))
        uow.native_forwards.record_first_hop_result(
            batch.id,
            marker_message_id=500,
            bridge_message_ids=(601, 602),
        )
        uow.commit()
        return batch


def _event(client, message_id, *, sender_id=7001, text="", forwarded=False):
    message = SimpleNamespace(id=message_id, fwd_from=SimpleNamespace() if forwarded else None)
    return SimpleNamespace(sender_id=sender_id, raw_text=text, message=message, client=client, is_private=True)


def test_bridge_consumes_marker_and_writes_each_final_reply_mapping():
    factory = _factory()
    batch = _prepared_batch(factory)
    client = BotClient()
    handler = NativeForwardBridgeHandler(factory)
    marker = marker_text(SimpleNamespace(marker_token=batch.marker_token, expected_count=2))

    assert asyncio.run(handler.handle_message(_event(client, 500, text=marker))) is True
    assert asyncio.run(handler.handle_message(_event(client, 601, forwarded=True))) is True
    assert asyncio.run(handler.handle_message(_event(client, 602, forwarded=True))) is True

    assert client.calls[0][0:2] == ("header", 1001)
    assert "来源受 Telegram 隐私保护，仅能显示名称" in client.calls[0][2]
    assert client.calls[1] == ("forward", 1001, (601, 602), 7001)
    with UnitOfWork(factory) as uow:
        stored = uow.native_forwards.get(batch.id)
        assert stored.status == "sent"
        assert stored.header_bot_message_id == 700
        user_id = uow.users.get_by_telegram_id(1001).id
        assert uow.mappings.get_by_bot_message(user_id, 801).source_message_id == 11
        assert uow.mappings.get_by_bot_message(user_id, 802).source_message_id == 12


def test_bridge_uses_bot_dialog_ids_when_they_differ_from_first_hop_ids():
    factory = _factory()
    batch = _prepared_batch(factory)
    client = BotClient()
    handler = NativeForwardBridgeHandler(factory)
    marker = marker_text(SimpleNamespace(marker_token=batch.marker_token, expected_count=2))

    asyncio.run(handler.handle_message(_event(client, 1500, text=marker)))
    asyncio.run(handler.handle_message(_event(client, 1601, forwarded=True)))
    asyncio.run(handler.handle_message(_event(client, 1602, forwarded=True)))

    assert client.calls[1] == ("forward", 1001, (1601, 1602), 7001)
    with UnitOfWork(factory) as uow:
        assert uow.native_forwards.get(batch.id).status == "sent"
        assert [item.bridge_message_id for item in uow.native_forwards.list_items(batch.id)] == [1601, 1602]


def test_known_bound_sender_orphan_forward_is_quarantined_not_routed():
    factory = _factory()
    _prepared_batch(factory)
    handler = NativeForwardBridgeHandler(factory)

    consumed = asyncio.run(handler.handle_message(_event(BotClient(), 601, forwarded=True)))

    assert consumed is True
    with UnitOfWork(factory) as uow:
        quarantine = uow.session.query(NativeForwardBridgeQuarantineModel).one()
        assert quarantine.failure_code == "bridge_orphan_forward"
        assert "payload" not in quarantine.__table__.columns


def test_duplicate_bot_dialog_message_is_idempotent():
    factory = _factory()
    batch = _prepared_batch(factory)
    handler = NativeForwardBridgeHandler(factory)
    client = BotClient()
    marker = marker_text(SimpleNamespace(marker_token=batch.marker_token, expected_count=2))
    asyncio.run(handler.handle_message(_event(client, 1500, text=marker)))
    asyncio.run(handler.handle_message(_event(client, 1601, forwarded=True)))
    asyncio.run(handler.handle_message(_event(client, 1601, forwarded=True)))

    assert client.calls == []
    asyncio.run(handler.handle_message(_event(client, 1602, forwarded=True)))

    assert client.calls[1] == ("forward", 1001, (1601, 1602), 7001)
    with UnitOfWork(factory) as uow:
        assert uow.native_forwards.get(batch.id).status == "sent"
        assert uow.session.query(NativeForwardBridgeQuarantineModel).count() == 0


def test_prepare_final_commits_account_unavailable_failure():
    factory = _factory()
    batch = _prepared_batch(factory)
    with UnitOfWork(factory) as uow:
        uow.native_forwards.mark_awaiting_bot(batch.marker_token, 7001, marker_message_id=500)
        uow.native_forwards.append_bridge_message(7001, 601, "linked")
        uow.native_forwards.append_bridge_message(7001, 602, "linked")
        uow.accounts.mark_reauth_required(batch.bound_tg_account_id)
        uow.commit()

    assert NativeForwardBridgeHandler(factory)._prepare_final(batch.marker_token) is None
    with UnitOfWork(factory) as uow:
        stored = uow.native_forwards.get(batch.id)
        assert stored.status == "failed"
        assert stored.failure_code == "account_unavailable"


def test_marker_count_mismatch_is_quarantined_before_generic_router():
    factory = _factory()
    batch = _prepared_batch(factory)
    handler = NativeForwardBridgeHandler(factory)
    bad_marker = marker_text(SimpleNamespace(marker_token=batch.marker_token, expected_count=1))

    assert asyncio.run(handler.handle_message(_event(BotClient(), 500, text=bad_marker))) is True

    with UnitOfWork(factory) as uow:
        quarantine = uow.session.query(NativeForwardBridgeQuarantineModel).one()
        assert quarantine.failure_code == "bridge_marker_mismatch"


def test_final_count_mismatch_stays_uncertain_without_reply_mappings():
    factory = _factory()
    batch = _prepared_batch(factory)
    client = BotClient(final_ids=(801,))
    handler = NativeForwardBridgeHandler(factory)
    marker = marker_text(SimpleNamespace(marker_token=batch.marker_token, expected_count=2))

    asyncio.run(handler.handle_message(_event(client, 500, text=marker)))
    asyncio.run(handler.handle_message(_event(client, 601, forwarded=True)))
    asyncio.run(handler.handle_message(_event(client, 602, forwarded=True)))

    with UnitOfWork(factory) as uow:
        stored = uow.native_forwards.get(batch.id)
        assert stored.status == "uncertain"
        assert stored.failure_code == "bridge_item_count_mismatch"
        user_id = uow.users.get_by_telegram_id(1001).id
        assert uow.mappings.get_by_bot_message(user_id, 801) is None


def test_bridge_defers_grouped_new_messages_to_one_album_consumption():
    factory = _factory()
    batch = _prepared_batch(factory)
    client = BotClient()
    handler = NativeForwardBridgeHandler(factory)
    marker = marker_text(SimpleNamespace(marker_token=batch.marker_token, expected_count=2))
    asyncio.run(handler.handle_message(_event(client, 500, text=marker)))
    first = _event(client, 601, forwarded=True)
    second = _event(client, 602, forwarded=True)
    first.message.grouped_id = 77
    second.message.grouped_id = 77

    assert asyncio.run(handler.handle_message(first)) is True
    assert asyncio.run(handler.handle_message(second)) is True
    assert client.calls == []

    album = SimpleNamespace(sender_id=7001, is_private=True, messages=[second.message, first.message], client=client)
    assert asyncio.run(handler.handle_album(album)) is True

    with UnitOfWork(factory) as uow:
        assert uow.native_forwards.get(batch.id).status == "sent"
        assert uow.session.query(NativeForwardBridgeQuarantineModel).count() == 0


def test_out_of_order_bridge_updates_preserve_batch_sequence_for_final_forward():
    factory = _factory()
    batch = _prepared_batch(factory)
    client = BotClient()
    handler = NativeForwardBridgeHandler(factory)
    marker = marker_text(SimpleNamespace(marker_token=batch.marker_token, expected_count=2))
    asyncio.run(handler.handle_message(_event(client, 500, text=marker)))

    asyncio.run(handler.handle_message(_event(client, 602, forwarded=True)))
    asyncio.run(handler.handle_message(_event(client, 601, forwarded=True)))

    assert client.calls[1] == ("forward", 1001, (601, 602), 7001)
    with UnitOfWork(factory) as uow:
        stored = uow.native_forwards.get(batch.id)
        assert stored.status == "sent"


def test_header_transport_uncertainty_leaves_terminal_evidence_and_failure_notice():
    factory = _factory()
    batch = _prepared_batch(factory)
    client = HeaderFailsOnceClient()
    handler = NativeForwardBridgeHandler(factory)
    marker = marker_text(SimpleNamespace(marker_token=batch.marker_token, expected_count=2))

    asyncio.run(handler.handle_message(_event(client, 500, text=marker)))
    asyncio.run(handler.handle_message(_event(client, 601, forwarded=True)))
    asyncio.run(handler.handle_message(_event(client, 602, forwarded=True)))

    assert client.calls == [("failure", 1001, "原生转发失败：bridge_transport_unknown")]
    with UnitOfWork(factory) as uow:
        stored = uow.native_forwards.get(batch.id)
        assert stored.status == "uncertain"
        assert stored.failure_code == "bridge_transport_unknown"
