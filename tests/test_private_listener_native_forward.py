import asyncio
from types import SimpleNamespace

import pytest

from tg_v_chat.domain import DeliveryFailure, MediaKind
from tg_v_chat.telegram.private_listener.event_parsing import (
    async_native_forward_batch_from_album,
    async_native_forward_message_from_event,
)
from tg_v_chat.telegram.private_listener.process import BoundListenerSession


def _binding():
    return BoundListenerSession(7, 42, "+15550000001", "小号A", "example", "primary", "session")


def _message(message_id: int, *, kind: str, grouped_id=None):
    values = {
        "id": message_id,
        "grouped_id": grouped_id,
        "date": None,
        "photo": None,
        "sticker": None,
        "video": None,
        "video_note": None,
        "audio": None,
        "voice": None,
        "media": object(),
    }
    values[kind] = object()
    return SimpleNamespace(**values)


def _event(message):
    return SimpleNamespace(
        chat_id=88,
        sender_id=88,
        raw_text="caption",
        input_chat=SimpleNamespace(access_hash=123),
        input_sender=None,
        message=message,
        is_private=True,
    )


@pytest.mark.parametrize(
    ("telegram_kind", "expected_kind"),
    [
        ("photo", MediaKind.PHOTO),
        ("video", MediaKind.VIDEO),
        ("video_note", MediaKind.VIDEO_NOTE),
        ("audio", MediaKind.AUDIO),
        ("voice", MediaKind.VOICE),
        ("sticker", MediaKind.STICKER),
    ],
)
def test_native_parser_keeps_supported_media_without_downloading(telegram_kind, expected_kind):
    parsed = asyncio.run(async_native_forward_message_from_event(_binding(), _event(_message(11, kind=telegram_kind))))

    assert parsed.media_kind is expected_kind
    assert parsed.artifacts == ()
    assert parsed.payload == "caption"


def test_native_album_parser_keeps_media_order_without_media_store():
    album = SimpleNamespace(
        is_private=True,
        chat_id=88,
        sender_id=88,
        messages=[_message(12, kind="video", grouped_id=77), _message(11, kind="photo", grouped_id=77)],
    )

    parsed = asyncio.run(async_native_forward_batch_from_album(_binding(), album))

    assert [message.source_message_id for message in parsed.messages] == [11, 12]
    assert [message.media_kind for message in parsed.messages] == [MediaKind.PHOTO, MediaKind.VIDEO]
    assert all(message.artifacts == () for message in parsed.messages)


def test_native_parser_rejects_unsupported_media_without_copy_fallback():
    message = _message(11, kind="photo")
    message.photo = None

    with pytest.raises(DeliveryFailure, match="native_forward_unsupported_media"):
        asyncio.run(async_native_forward_message_from_event(_binding(), _event(message)))
