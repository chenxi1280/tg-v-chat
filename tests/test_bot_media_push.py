from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tg_v_chat.domain import DeliveryUncertain, IncomingPrivateBatch, IncomingPrivateMessage, MediaArtifact, MediaKind
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.telegram.telethon_clients.gateway import TelethonBotGateway


class NoopSender:
    def send_reply(self, _session_slot, _peer, _reply):
        raise AssertionError("incoming push must not send user-session replies")


class RecordingBotClient:
    def __init__(self, ids=None):
        self.ids = ids or [501, 502, 503]
        self.messages = []
        self.files = []
        self.calls = []

    async def send_message(self, user_id, text):
        self.messages.append((user_id, text))
        self.calls.append("message")
        return type("Sent", (), {"id": self.ids.pop(0)})()

    async def send_file(self, user_id, file, caption=None):
        self.files.append((user_id, file, caption))
        self.calls.append("file")
        if isinstance(file, list):
            return [type("Sent", (), {"id": self.ids.pop(0)})() for _item in file]
        return type("Sent", (), {"id": self.ids.pop(0)})()


class LoopCheckingBotClient(RecordingBotClient):
    def __init__(self, expected_loop):
        super().__init__([700, 701])
        self.expected_loop = expected_loop

    async def send_file(self, user_id, file, caption=None):
        assert asyncio.get_running_loop() is self.expected_loop
        return await super().send_file(user_id, file, caption)


def _factory():
    session_factory = create_session_factory("sqlite:///:memory:")
    init_db(session_factory)
    return session_factory


def _seed_account(factory):
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(1001)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.accounts.mark_active(account.id)
        uow.commit()
        return account.id


def _artifact(store, name, sequence):
    artifact = store.write_bytes(b"image", file_name=name, mime_type="image/jpeg", media_kind=MediaKind.PHOTO)
    return MediaArtifact(
        artifact.storage_key,
        artifact.file_name,
        artifact.mime_type,
        artifact.byte_size,
        artifact.media_kind,
        sequence,
    )


def test_gateway_uses_send_message_for_text_and_send_file_for_media(tmp_path):
    client = RecordingBotClient()
    gateway = TelethonBotGateway.from_client(client, MediaStore(tmp_path))
    text = IncomingPrivateMessage(1, 88, 10, MediaKind.TEXT, "hello", None, 0)
    photo = IncomingPrivateMessage(
        1,
        88,
        11,
        MediaKind.PHOTO,
        "caption",
        None,
        1,
        artifacts=(_artifact(MediaStore(tmp_path), "a.jpg", 1),),
    )

    assert gateway.push_private_message(1001, text) == 501
    assert gateway.push_private_message(1001, photo) == 503

    assert client.messages[0][1].endswith("内容：hello")
    assert len(client.messages) == 2
    assert client.messages[1][1].endswith("内容：caption")
    assert client.files[0][2] is None
    assert client.calls[-2:] == ["message", "file"]


def test_gateway_from_client_schedules_media_send_on_owner_loop(tmp_path):
    async def run():
        loop = asyncio.get_running_loop()
        store = MediaStore(tmp_path)
        artifact = _artifact(store, "a.jpg", 1)
        client = LoopCheckingBotClient(loop)
        gateway = TelethonBotGateway.from_client(client, store, loop=loop)
        message = IncomingPrivateMessage(1, 88, 11, MediaKind.PHOTO, "caption", None, 1, artifacts=(artifact,))

        sent_id = await asyncio.to_thread(gateway.push_private_message, 1001, message)

        assert sent_id == 701

    asyncio.run(run())


def test_successful_media_push_releases_artifact_file_and_metadata(tmp_path):
    factory = _factory()
    account_id = _seed_account(factory)
    store = MediaStore(tmp_path)
    artifact = _artifact(store, "1.jpg", 1)
    message = IncomingPrivateMessage(
        account_id, 88, 11, MediaKind.PHOTO, "caption", None, 1, artifacts=(artifact,)
    )

    with UnitOfWork(factory) as uow:
        PrivateRelayService(
            uow,
            TelethonBotGateway.from_client(RecordingBotClient([600, 601]), store),
            NoopSender(),
            media_store=store,
        ).receive_private_message(message)

    assert not store.path_for(artifact).exists()
    with UnitOfWork(factory) as uow:
        assert uow.media_artifacts.get_by_storage_key(artifact.storage_key).status == "released"


def test_duplicate_media_push_releases_new_artifact_without_resent(tmp_path):
    factory = _factory()
    account_id = _seed_account(factory)
    store = MediaStore(tmp_path)
    bot = RecordingBotClient([601, 602])
    first = _artifact(store, "1.jpg", 1)
    duplicate = _artifact(store, "1-copy.jpg", 1)
    first_message = IncomingPrivateMessage(
        account_id, 88, 11, MediaKind.PHOTO, "caption", None, 1, artifacts=(first,)
    )
    duplicate_message = IncomingPrivateMessage(
        account_id, 88, 11, MediaKind.PHOTO, "caption", None, 1, artifacts=(duplicate,)
    )

    with UnitOfWork(factory) as uow:
        service = PrivateRelayService(uow, TelethonBotGateway.from_client(bot, store), NoopSender(), media_store=store)
        service.receive_private_message(first_message)

    with UnitOfWork(factory) as uow:
        service = PrivateRelayService(uow, TelethonBotGateway.from_client(bot, store), NoopSender(), media_store=store)
        result = service.receive_private_message(duplicate_message)

    assert result.duplicate is True
    assert len(bot.files) == 1
    assert not store.path_for(duplicate).exists()
    with UnitOfWork(factory) as uow:
        assert uow.media_artifacts.get_by_storage_key(duplicate.storage_key).status == "released"


def test_relay_pushes_album_as_ordered_batch_and_maps_each_bot_id(tmp_path):
    factory = _factory()
    account_id = _seed_account(factory)
    store = MediaStore(tmp_path)
    client = RecordingBotClient([600, 601, 602])
    bot = TelethonBotGateway.from_client(client, store)
    batch = IncomingPrivateBatch(
        (
            IncomingPrivateMessage(
                account_id, 88, 11, MediaKind.PHOTO, "first", "g1", 1, artifacts=(_artifact(store, "1.jpg", 1),)
            ),
            IncomingPrivateMessage(
                account_id, 88, 12, MediaKind.PHOTO, "second", "g1", 2, artifacts=(_artifact(store, "2.jpg", 2),)
            ),
        )
    )

    with UnitOfWork(factory) as uow:
        results = PrivateRelayService(uow, bot, NoopSender()).receive_private_batch(batch)

    assert [item.bot_message_id for item in results] == [601, 602]
    assert client.messages[0][1].endswith("内容：first")
    assert client.files[0][2] is None
    assert client.calls == ["message", "file"]
    with UnitOfWork(factory) as uow:
        assert uow.mappings.get_by_bot_message(1, 601).source_message_id == 11
        assert uow.mappings.get_by_bot_message(1, 602).source_message_id == 12


def test_stale_sending_media_push_is_uncertain_and_not_resent(tmp_path):
    factory = _factory()
    account_id = _seed_account(factory)
    store = MediaStore(tmp_path)
    message = IncomingPrivateMessage(
        account_id, 88, 11, MediaKind.PHOTO, "caption", None, 1, artifacts=(_artifact(store, "1.jpg", 1),)
    )
    with UnitOfWork(factory) as uow:
        relay, _created = uow.relays.create_or_get(message)
        uow.pushes.create_pending(relay.id, 1, f"push:{relay.id}")
        assert uow.pushes.claim(f"push:{relay.id}")
        uow.commit()

    client = RecordingBotClient([601])
    with pytest.raises(DeliveryUncertain):
        with UnitOfWork(factory) as uow:
            PrivateRelayService(uow, TelethonBotGateway.from_client(client, store), NoopSender()).receive_private_message(message)

    assert client.files == []
