import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from tg_v_chat.domain import (
    DeveloperSlot,
    FirstHopForwardResult,
    IncomingPrivateMessage,
    MediaKind,
    OutgoingReply,
    SessionStatus,
)
from tg_v_chat.services.native_forward import NativeForwardCollector, NativeForwardDispatchService
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.models import RelayMessageModel
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.private_listener.native_forward import marker_text
from tg_v_chat.telegram.telethon_clients.forward_bridge import NativeForwardBridgeHandler


class UserSessionForwarder:
    def __init__(self):
        self.requests = []

    def forward_batch(self, request):
        self.requests.append(request)
        return FirstHopForwardResult(request.expected_count)


class BotClient:
    async def send_message(self, _target, _text):
        return SimpleNamespace(id=700)

    async def forward_messages(self, _target, _message_ids, *, from_peer):
        assert from_peer == 7001
        return [SimpleNamespace(id=801), SimpleNamespace(id=802)]


class ReplySender:
    def __init__(self):
        self.calls = []

    def send_reply(self, slot, peer, reply):
        self.calls.append((slot, peer, reply))
        return 9001


class UnusedBotGateway:
    def push_private_message(self, *_args):
        raise AssertionError("V2 must not fall back to copied Bot push")


def _factory():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    return factory


def _event(client, message_id, *, text="", forwarded=False):
    return SimpleNamespace(
        sender_id=7001,
        raw_text=text,
        message=SimpleNamespace(id=message_id, fwd_from=SimpleNamespace(from_id=1) if forwarded else None),
        client=client,
        is_private=True,
    )


def test_native_forward_two_hop_creates_replyable_mapping_without_copy_push():
    factory = _factory()
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(1001)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.accounts.mark_active(account.id)
        uow.accounts.update_telegram_identity(account.id, 7001)
        uow.sessions.create(
            account.id,
            slot=DeveloperSlot.PRIMARY,
            encrypted_session="session",
            status=SessionStatus.ACTIVE,
        )
        collector = NativeForwardCollector(uow, token_factory=lambda: "marker-token")
        first = collector.ingest(
            IncomingPrivateMessage(account.id, 88, 11, MediaKind.VIDEO, "caption", None, 1, 9901),
            now=now,
        )
        collector.ingest(
            IncomingPrivateMessage(account.id, 88, 12, MediaKind.AUDIO, "audio", None, 1, 9901),
            now=now,
        )
        uow.native_forwards.seal(first.batch.id)
        uow.commit()

    first_hop = UserSessionForwarder()
    NativeForwardDispatchService(factory, first_hop, bridge_timeout_seconds=30, now=lambda: now).dispatch(first.batch.id)
    bridge = NativeForwardBridgeHandler(factory)
    client = BotClient()
    marker = marker_text(SimpleNamespace(marker_token="marker-token", expected_count=2))

    asyncio.run(bridge.handle_message(_event(client, 500, text=marker)))
    asyncio.run(bridge.handle_message(_event(client, 601, forwarded=True)))
    asyncio.run(bridge.handle_message(_event(client, 602, forwarded=True)))

    reply_sender = ReplySender()
    with UnitOfWork(factory) as uow:
        result = PrivateRelayService(uow, UnusedBotGateway(), reply_sender).handle_bot_reply(
            OutgoingReply(1001, 900, 801, MediaKind.TEXT, "收到")
        )

    assert first_hop.requests[0].source_message_ids == (11, 12)
    assert result.sent_message_id == 9001
    assert reply_sender.calls[0][1].id == 88
    assert reply_sender.calls[0][1].access_hash == 9901


def test_official_777000_keeps_the_original_source_id_without_copy_push():
    factory = _factory()
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(1001)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.accounts.mark_active(account.id)
        uow.accounts.update_telegram_identity(account.id, 7001)
        collected = NativeForwardCollector(uow, token_factory=lambda: "official-marker").ingest(
            IncomingPrivateMessage(account.id, 777000, 77, MediaKind.TEXT, "Login code: 654321", None, 1, 9901),
            now=now,
        )
        assert uow.session.get(RelayMessageModel, collected.item.relay_message_id).payload == "Login code: 654321"
        uow.native_forwards.seal(collected.batch.id)
        uow.commit()

    forwarder = UserSessionForwarder()
    NativeForwardDispatchService(factory, forwarder, bridge_timeout_seconds=30, now=lambda: now).dispatch(collected.batch.id)

    assert forwarder.requests[0].source_peer.id == 777000
    assert forwarder.requests[0].source_message_ids == (77,)
