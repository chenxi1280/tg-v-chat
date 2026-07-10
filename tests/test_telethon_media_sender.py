from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace

from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import (
    DeveloperSlot,
    IncomingPrivateMessage,
    MediaKind,
    OutgoingReply,
    SessionFailure,
    SessionSlotRef,
    SessionStatus,
    TelegramPeer,
)
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.telegram.telethon_clients.config import DeveloperAppConfig
from tg_v_chat.telegram.telethon_clients.gateway import TelethonReplySender


class RecordingClient:
    calls = []

    def __init__(self, *_args):
        pass

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def send_message(self, peer, payload):
        self.calls.append(("message", peer, payload))
        return SimpleNamespace(id=901)

    async def send_file(self, peer, file, caption=None):
        self.calls.append(("file", peer, file, caption))
        if isinstance(file, list):
            return [SimpleNamespace(id=902), SimpleNamespace(id=903)]
        return SimpleNamespace(id=902)


def _install_telethon(monkeypatch):
    telethon = ModuleType("telethon")
    telethon.TelegramClient = RecordingClient
    sessions = ModuleType("telethon.sessions")
    sessions.StringSession = lambda value=None: value
    monkeypatch.setitem(__import__("sys").modules, "telethon", telethon)
    monkeypatch.setitem(__import__("sys").modules, "telethon.sessions", sessions)


def _slot(cipher):
    return SessionSlotRef(1, 1, DeveloperSlot.PRIMARY, cipher.encrypt("session"))


def test_reply_sender_uses_send_message_for_text_and_send_file_for_media(monkeypatch, tmp_path):
    _install_telethon(monkeypatch)
    cipher = SessionCipher("test-key")
    store = MediaStore(tmp_path)
    artifact = store.write_bytes(b"photo", file_name="a.jpg", mime_type="image/jpeg", media_kind=MediaKind.PHOTO)
    sender = TelethonReplySender({DeveloperSlot.PRIMARY: DeveloperAppConfig(1, "hash")}, cipher, media_store=store)

    text_id = sender.send_reply(_slot(cipher), TelegramPeer(88), OutgoingReply(1001, 1, 500, MediaKind.TEXT, "hello"))
    media_id = sender.send_reply(
        _slot(cipher),
        TelegramPeer(88),
        OutgoingReply(
            1001,
            2,
            500,
            MediaKind.PHOTO,
            "caption",
            artifacts=(artifact,),
        ),
    )

    assert text_id == 901
    assert media_id == 902
    assert RecordingClient.calls[0] == ("message", 88, "hello")
    assert RecordingClient.calls[1][0] == "file"
    assert RecordingClient.calls[1][2] == str(store.path_for(artifact))
    assert RecordingClient.calls[1][3] == "caption"


def test_reply_sender_accepts_album_send_file_result_list(monkeypatch, tmp_path):
    _install_telethon(monkeypatch)
    cipher = SessionCipher("test-key")
    store = MediaStore(tmp_path)
    first = store.write_bytes(b"1", file_name="1.jpg", mime_type="image/jpeg", media_kind=MediaKind.PHOTO)
    second = store.write_bytes(b"2", file_name="2.jpg", mime_type="image/jpeg", media_kind=MediaKind.PHOTO)
    sender = TelethonReplySender({DeveloperSlot.PRIMARY: DeveloperAppConfig(1, "hash")}, cipher, media_store=store)

    sent_id = sender.send_reply(
        _slot(cipher),
        TelegramPeer(88),
        OutgoingReply(1001, 2, 500, MediaKind.PHOTO, "album", artifacts=(first, second)),
    )

    assert sent_id == 902
    assert RecordingClient.calls[-1][2] == [str(store.path_for(first)), str(store.path_for(second))]


class Bot:
    def push_private_message(self, *_args):
        return 500

    def notify_failure(self, *_args):
        raise AssertionError("unexpected notify")


class Sender:
    def __init__(self, sent_id):
        self.sent_id = sent_id

    def send_reply(self, *_args):
        return self.sent_id


class FailingSender:
    def send_reply(self, *_args):
        raise SessionFailure("expired")


def test_outgoing_media_reply_persists_and_releases_artifact(tmp_path):
    factory, store, artifact = _outgoing_media_setup(tmp_path)

    with UnitOfWork(factory) as uow:
        reply = OutgoingReply(1001, 77, 500, MediaKind.PHOTO, "caption", artifacts=(artifact,))
        PrivateRelayService(uow, Bot(), Sender(901), media_store=store).handle_bot_reply(reply)

    with UnitOfWork(factory) as uow:
        row = uow.media_artifacts.get_by_storage_key(artifact.storage_key)
        assert row.status == "released"
    assert not store.path_for(artifact).exists()


def test_duplicate_sent_outgoing_media_reply_discards_new_artifact(tmp_path):
    factory, store, first = _outgoing_media_setup(tmp_path)
    duplicate = store.write_bytes(b"photo2", file_name="b.jpg", mime_type="image/jpeg", media_kind=MediaKind.PHOTO)

    with UnitOfWork(factory) as uow:
        reply = OutgoingReply(1001, 77, 500, MediaKind.PHOTO, "caption", artifacts=(first,))
        PrivateRelayService(uow, Bot(), Sender(901), media_store=store).handle_bot_reply(reply)

    with UnitOfWork(factory) as uow:
        reply = OutgoingReply(1001, 77, 500, MediaKind.PHOTO, "caption", artifacts=(duplicate,))
        result = PrivateRelayService(uow, Bot(), Sender(902), media_store=store).handle_bot_reply(reply)

    assert result.duplicate is True
    assert result.sent_message_id == 901
    assert not store.path_for(duplicate).exists()
    with UnitOfWork(factory) as uow:
        assert uow.media_artifacts.get_by_storage_key(duplicate.storage_key) is None


def test_outgoing_media_reply_releases_artifact_after_all_sessions_fail(tmp_path):
    factory, store, artifact = _outgoing_media_setup(tmp_path)

    with UnitOfWork(factory) as uow:
        reply = OutgoingReply(1001, 78, 500, MediaKind.PHOTO, "caption", artifacts=(artifact,))
        service = PrivateRelayService(uow, Bot(), FailingSender(), media_store=store)
        try:
            service.handle_bot_reply(reply)
        except SessionFailure:
            pass

    with UnitOfWork(factory) as uow:
        row = uow.media_artifacts.get_by_storage_key(artifact.storage_key)
        assert row.status == "released"
    assert not store.path_for(artifact).exists()


def _outgoing_media_setup(tmp_path):
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    store = MediaStore(tmp_path)
    artifact = store.write_bytes(b"photo", file_name="a.jpg", mime_type="image/jpeg", media_kind=MediaKind.PHOTO)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(1001)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.sessions.create(
            account.id,
            slot=DeveloperSlot.PRIMARY,
            encrypted_session="encrypted",
            status=SessionStatus.ACTIVE,
        )
        uow.accounts.mark_active(account.id)
        relay = uow.relays.create_or_get(IncomingPrivateMessage(account.id, 88, 11, MediaKind.TEXT, "hi", None, 0))[0]
        uow.mappings.create(500, relay, user.id)
        uow.commit()
    return factory, store, artifact
