from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tg_v_chat.domain import IncomingPrivateBatch, MediaKind
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.telegram.private_listener import BoundListenerSession
from tg_v_chat.telegram.private_listener.event_parsing import (
    async_private_batch_from_album,
    async_private_message_from_event,
)
from tg_v_chat.telegram.private_listener.process import private_album_event_builder, private_message_event_builder
from tg_v_chat.telegram.private_listener.process import _album_handler


def _binding():
    return BoundListenerSession(7, 42, "+15550000001", "小号A", "example", "primary", "session")


class DownloadableMessage:
    def __init__(self, message_id, *, grouped_id=None, photo=True, sticker=False, fail=False):
        self.id = message_id
        self.grouped_id = grouped_id
        self.photo = object() if photo else None
        self.sticker = object() if sticker else None
        self.fail = fail
        self.date = None

    async def download_media(self, file):
        if self.fail:
            raise RuntimeError("download failed")
        with open(file, "wb") as handle:
            handle.write(f"media-{self.id}".encode())


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


def test_listener_uses_incoming_builders_and_suppresses_grouped_new_message(tmp_path):
    assert private_message_event_builder().incoming is True
    assert private_album_event_builder() is not None
    store = MediaStore(tmp_path)
    grouped = _event(DownloadableMessage(10, grouped_id=99))

    assert asyncio.run(async_private_message_from_event(_binding(), grouped, media_store=store)) is None


def test_photo_event_downloads_artifact_without_placeholder_payload(tmp_path):
    store = MediaStore(tmp_path)
    message = asyncio.run(
        async_private_message_from_event(
            _binding(),
            _event(DownloadableMessage(10)),
            media_store=store,
        )
    )

    assert message.media_kind is MediaKind.PHOTO
    assert message.payload == "caption"
    assert message.artifacts[0].byte_size == len(b"media-10")
    assert "[photo]" not in message.payload


def test_album_is_sorted_and_fails_whole_batch_on_download_failure(tmp_path):
    store = MediaStore(tmp_path)
    album = SimpleNamespace(
        is_private=True,
        chat_id=88,
        sender_id=88,
        messages=[
            DownloadableMessage(12, grouped_id=77),
            DownloadableMessage(11, grouped_id=77),
        ],
    )

    batch = asyncio.run(async_private_batch_from_album(_binding(), album, media_store=store))

    assert isinstance(batch, IncomingPrivateBatch)
    assert [item.source_message_id for item in batch.messages] == [11, 12]
    assert [item.sequence for item in batch.messages] == [1, 2]
    assert all(item.artifacts for item in batch.messages)

    broken = SimpleNamespace(
        is_private=True,
        chat_id=88,
        sender_id=88,
        messages=[
            DownloadableMessage(13, grouped_id=88),
            DownloadableMessage(14, grouped_id=88, fail=True),
        ],
    )
    with pytest.raises(RuntimeError, match="download failed"):
        asyncio.run(async_private_batch_from_album(_binding(), broken, media_store=MediaStore(tmp_path / "b")))
    assert list((tmp_path / "b").iterdir()) == []


def test_album_handler_ignores_non_private_events(tmp_path):
    calls = []

    class Gateway:
        def push_private_batch(self, *_args):
            calls.append("push")

    handler = _album_handler(_binding(), object(), Gateway(), media_store=MediaStore(tmp_path))
    asyncio.run(handler(SimpleNamespace(is_private=False, messages=[])))

    assert calls == []
