from types import SimpleNamespace

from tg_v_chat.domain import MediaKind
from tg_v_chat.telegram.private_listener import (
    BoundListenerSession,
    private_message_event_builder,
    private_message_from_event,
)


def test_private_text_event_is_converted_to_relay_message():
    event = SimpleNamespace(
        chat_id=149222,
        raw_text="11",
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
    assert message.source_message_id == 36
    assert message.media_kind is MediaKind.TEXT
    assert message.payload == "11"
    assert message.media_group_id is None
    assert message.sequence == 1


def test_private_listener_subscribes_to_incoming_and_outgoing_messages():
    builder = private_message_event_builder()

    assert builder.incoming is None
    assert builder.outgoing is None
