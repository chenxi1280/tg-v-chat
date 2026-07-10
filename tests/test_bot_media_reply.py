from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tg_v_chat.bot.handlers import BotReplyHandler
from tg_v_chat.bot.router import BotIncomingMessage, BotUpdateRouter
from tg_v_chat.domain import MediaKind
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.telegram.telethon_clients.bot_process import incoming_album_from_event, incoming_message_from_event


class AccountManagement:
    def handle_command(self, *_args):
        raise AssertionError("unexpected command")

    def handle_callback(self, *_args):
        raise AssertionError("unexpected callback")

    def handle_text(self, *_args):
        raise AssertionError("unexpected text")


class DownloadableMessage:
    id = 77
    photo = object()
    sticker = None
    media = object()
    raw_text = "caption"

    def __init__(self):
        self.reply_to = SimpleNamespace(reply_to_msg_id=500)

    async def download_media(self, file):
        with open(file, "wb") as handle:
            handle.write(b"photo")


def test_bot_photo_reply_downloads_artifact_before_router(tmp_path):
    event = SimpleNamespace(sender_id=1001, raw_text="caption", message=DownloadableMessage())

    incoming = asyncio.run(incoming_message_from_event(event, MediaStore(tmp_path)))

    assert incoming.media_kind is MediaKind.PHOTO
    assert incoming.text == "caption"
    assert incoming.artifacts[0].byte_size == len(b"photo")


def test_router_and_handler_preserve_reply_artifacts():
    captured = []

    def handle_reply(command):
        captured.append(command)

    router = BotUpdateRouter(handle_reply, AccountManagement())
    artifact = object()
    router.handle(
        BotIncomingMessage(
            1001,
            77,
            500,
            "caption",
            media_kind=MediaKind.PHOTO,
            artifacts=(artifact,),
        )
    )

    assert captured[0].media_kind is MediaKind.PHOTO
    assert captured[0].artifacts == (artifact,)

    handled = []
    handler = BotReplyHandler(lambda: SimpleNamespace(__enter__=lambda _s: handled, __exit__=lambda *_a: None))
    reply = handler._reply_from_command(captured[0])
    assert reply.artifacts == (artifact,)


def test_bot_reply_unsupported_media_is_rejected(tmp_path):
    message = SimpleNamespace(id=78, photo=None, sticker=None, media=object(), reply_to=None)
    event = SimpleNamespace(sender_id=1001, raw_text="", message=message)

    with pytest.raises(RuntimeError, match="unsupported"):
        asyncio.run(incoming_message_from_event(event, MediaStore(tmp_path)))


def test_bot_album_reply_downloads_ordered_artifacts_with_same_mapping(tmp_path):
    class AlbumMessage(DownloadableMessage):
        def __init__(self, message_id):
            super().__init__()
            self.id = message_id

    event = SimpleNamespace(
        sender_id=1001,
        raw_text="album caption",
        messages=[AlbumMessage(80), AlbumMessage(79)],
        is_private=True,
    )

    incoming = asyncio.run(incoming_album_from_event(event, MediaStore(tmp_path)))

    assert incoming.message_id == 79
    assert incoming.reply_to_message_id == 500
    assert incoming.text == "album caption"
    assert [artifact.sequence for artifact in incoming.artifacts] == [1, 2]
    assert len(incoming.artifacts) == 2
