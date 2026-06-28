import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from tg_v_chat.domain import MediaKind
from tg_v_chat.telegram.private_listener import (
    BoundListenerSession,
    _format_push_message,
    async_private_message_from_event,
    private_message_event_builder,
    private_message_from_event,
)


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
        developer_slot="primary",
        session_string="session",
    )

    message = private_message_from_event(binding, event)

    assert message.bound_tg_account_id == 7
    assert message.peer_id == 149222
    assert message.peer_access_hash == 987654321
    assert message.sender_name is None
    assert message.message_time is None
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
        developer_slot="primary",
        session_string="session",
    )

    message = asyncio.run(async_private_message_from_event(binding, Event()))

    assert message.peer_access_hash == 222333444


def test_async_private_event_reads_sender_name_and_message_time():
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
        developer_slot="primary",
        session_string="session",
    )

    message = asyncio.run(async_private_message_from_event(binding, Event()))

    assert message.sender_name == "洋芋 测试"
    assert message.message_time == sent_at


def test_push_message_display_shows_name_content_and_time_only():
    message = private_message_from_event(
        BoundListenerSession(7, 42, "+19525920433", "primary", "session"),
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
            "message_time": datetime(2026, 6, 28, 16, 12, 55, tzinfo=timezone.utc),
        }
    )

    formatted = _format_push_message(message)

    assert formatted == "发送人：洋芋\n消息：12131\n时间：2026-06-29 00:12:55"
    assert "账号 ID" not in formatted
    assert "来源 ID" not in formatted
    assert "消息 ID" not in formatted
    assert "类型" not in formatted
