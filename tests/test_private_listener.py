import asyncio
import sys
from datetime import datetime, timezone
from types import ModuleType
from types import SimpleNamespace

from tg_v_chat.domain import DeveloperSlot, MediaKind
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.private_listener import (
    BoundListenerSession,
    _format_push_message,
    async_private_message_from_event,
    private_message_event_builder,
    private_message_from_event,
)
from tg_v_chat.telegram.private_listener.process import _sync_bound_account_identity
from tg_v_chat.telegram.private_listener.process import TelethonPrivateListenerProcess
from tg_v_chat.telegram.telethon_clients.config import DeveloperAppConfig


def test_private_text_event_is_converted_to_relay_message():
    input_chat = SimpleNamespace(access_hash=987654321)
    event = SimpleNamespace(
        chat_id=149222,
        raw_text="11",
        input_chat=input_chat,
        input_sender=SimpleNamespace(access_hash=111111111),
        message=SimpleNamespace(id=36, grouped_id=None, photo=None, sticker=None),
    )
    binding = BoundListenerSession(
        account_id=7,
        system_user_id=42,
        phone_number="+19525920433",
        display_name="小号A",
        username="example_user",
        developer_slot="primary",
        session_string="session",
    )

    message = private_message_from_event(binding, event)

    _assert_private_text_identity(message)
    _assert_private_text_payload(message)


def _assert_private_text_identity(message) -> None:
    assert message.bound_tg_account_id == 7
    assert message.peer_id == 149222
    assert message.peer_access_hash == 987654321
    assert message.sender_name is None
    assert message.sent_at is None


def _assert_private_text_payload(message) -> None:
    assert message.source_message_id == 36
    assert message.media_kind is MediaKind.TEXT
    assert message.payload == "11"
    assert message.media_group_id is None
    assert message.sequence == 1


def test_private_listener_subscribes_to_incoming_and_outgoing_messages():
    builder = private_message_event_builder()

    assert builder.incoming is None
    assert builder.outgoing is None


def test_async_private_event_reads_access_hash_from_input_sender():
    class Event:
        chat_id = 149222
        sender_id = 149222
        raw_text = "11"
        input_chat = None
        input_sender = None
        message = SimpleNamespace(id=36, grouped_id=None, photo=None, sticker=None)

        async def get_input_sender(self):
            return SimpleNamespace(access_hash=222333444)

    binding = BoundListenerSession(
        account_id=7,
        system_user_id=42,
        phone_number="+19525920433",
        display_name="小号A",
        username="example_user",
        developer_slot="primary",
        session_string="session",
    )

    message = asyncio.run(async_private_message_from_event(binding, Event()))

    assert message.peer_access_hash == 222333444


def test_async_private_event_reads_sender_name_and_sent_at():
    sent_at = datetime(2026, 6, 28, 16, 12, 55, tzinfo=timezone.utc)

    class Event:
        chat_id = 149222
        sender_id = 149222
        raw_text = "hello"
        input_chat = None
        input_sender = None
        message = SimpleNamespace(id=36, date=sent_at, grouped_id=None, photo=None, sticker=None)

        async def get_input_sender(self):
            return SimpleNamespace(access_hash=222333444)

        async def get_sender(self):
            return SimpleNamespace(first_name="洋芋", last_name="测试", username="yangyuyan")

    binding = BoundListenerSession(
        account_id=7,
        system_user_id=42,
        phone_number="+19525920433",
        display_name="小号A",
        username="example_user",
        developer_slot="primary",
        session_string="session",
    )

    message = asyncio.run(async_private_message_from_event(binding, Event()))

    assert message.sender_name == "洋芋 测试"
    assert message.sent_at == sent_at


def test_listener_syncs_existing_account_identity_from_session_user():
    class Client:
        async def get_me(self):
            return SimpleNamespace(first_name="接收", last_name="账号", username="receiver_user")

    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(42)
        account = uow.accounts.create(user.id, "+19525920433")
        uow.commit()

    binding = BoundListenerSession(
        account_id=1,
        system_user_id=42,
        phone_number="+19525920433",
        display_name=None,
        username=None,
        developer_slot="primary",
        session_string="session",
    )

    updated = asyncio.run(_sync_bound_account_identity(Client(), binding, factory))

    assert updated.display_name == "接收 账号"
    assert updated.username == "receiver_user"
    with UnitOfWork(factory) as uow:
        account = uow.accounts.get(1)
        assert account.display_name == "接收 账号"
        assert account.username == "receiver_user"


def test_listener_registers_handler_when_identity_sync_fails(monkeypatch):
    class FakeClient:
        def __init__(self, _session, _api_id, _api_hash):
            self.handlers = []

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return True

        async def get_me(self):
            raise RuntimeError("profile query failed")

        def add_event_handler(self, handler, builder):
            self.handlers.append((handler, builder))

    telethon = ModuleType("telethon")
    telethon.TelegramClient = FakeClient
    telethon.events = SimpleNamespace(NewMessage=lambda: "new-message-builder")
    sessions = ModuleType("telethon.sessions")
    sessions.StringSession = lambda value=None: value
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.sessions", sessions)

    process = TelethonPrivateListenerProcess(
        {DeveloperSlot.PRIMARY: DeveloperAppConfig(1, "hash")},
        "bot-token",
        object(),
        session_cipher=object(),
    )
    binding = BoundListenerSession(
        account_id=7,
        system_user_id=42,
        phone_number="+19525920433",
        display_name=None,
        username=None,
        developer_slot="primary",
        session_string="session",
    )

    client = asyncio.run(process._start_user_client(binding, object()))

    assert len(client.handlers) == 1


def test_push_message_display_shows_sender_recipient_username_content_and_time():
    message = private_message_from_event(
        BoundListenerSession(
            account_id=7,
            system_user_id=42,
            phone_number="+19525920433",
            display_name="小号A",
            username="example_user",
            developer_slot="primary",
            session_string="session",
        ),
        SimpleNamespace(
            chat_id=149222,
            raw_text="12131",
            input_chat=SimpleNamespace(access_hash=987654321),
            input_sender=None,
            message=SimpleNamespace(
                id=36,
                date=datetime(2026, 6, 28, 16, 12, 55, tzinfo=timezone.utc),
                grouped_id=None,
                photo=None,
                sticker=None,
            ),
        ),
    )
    message = message.__class__(
        **{
            **message.__dict__,
            "sender_name": "洋芋",
            "sent_at": datetime(2026, 6, 28, 16, 12, 55, tzinfo=timezone.utc),
        }
    )

    formatted = _format_push_message(message)

    assert formatted == "\n".join(
        (
            "发送人：洋芋",
            "接收账号：小号A",
            "接收用户名：@example_user",
            "时间：2026-06-29 00:12:55",
            "内容：12131",
        )
    )
    assert "+19525920433" not in formatted
    assert "账号 ID" not in formatted
    assert "来源 ID" not in formatted
    assert "消息 ID" not in formatted
    assert "类型" not in formatted
