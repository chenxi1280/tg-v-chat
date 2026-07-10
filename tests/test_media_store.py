from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_v_chat.domain import MediaKind
from tg_v_chat.telegram.media_store import MediaStore


def test_media_store_requires_absolute_owner_only_root_and_sanitizes_names(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        MediaStore("relative/path")

    store = MediaStore(tmp_path / "media")
    artifact = store.write_bytes(
        b"abc",
        file_name="../bad/name.webp",
        mime_type="image/webp",
        media_kind=MediaKind.STICKER,
        sequence=1,
    )

    assert artifact.file_name == "bad_name.webp"
    assert artifact.storage_key not in artifact.file_name
    assert (tmp_path / "media").stat().st_mode & 0o777 == 0o700
    assert store.path_for(artifact).stat().st_mode & 0o777 == 0o600
    assert not store.part_path_for(artifact.storage_key).exists()


def test_media_store_download_finalizes_atomically_and_cleans_failed_part(tmp_path):
    store = MediaStore(tmp_path)

    class Message:
        async def download_media(self, file):
            Path(file).write_bytes(b"photo")

    artifact = asyncio.run(
        store.download_message_media(
            Message(),
            file_name="photo.jpg",
            mime_type="image/jpeg",
            media_kind=MediaKind.PHOTO,
            sequence=1,
        )
    )

    assert store.path_for(artifact).read_bytes() == b"photo"
    assert not store.part_path_for(artifact.storage_key).exists()

    class BrokenMessage:
        async def download_media(self, file):
            Path(file).write_bytes(b"partial")
            raise RuntimeError("download failed")

    with pytest.raises(RuntimeError, match="download failed"):
        asyncio.run(
            store.download_message_media(
                BrokenMessage(),
                file_name="photo.jpg",
                mime_type="image/jpeg",
                media_kind=MediaKind.PHOTO,
                sequence=1,
            )
        )
    assert not list(tmp_path.glob("*.part"))


def test_media_store_download_holds_part_lock_against_cleanup(tmp_path):
    store = MediaStore(tmp_path)
    observed = {}

    class SlowMessage:
        async def download_media(self, file):
            path = Path(file)
            path.write_bytes(b"partial")
            observed["removed"] = store.cleanup_unreferenced_parts(referenced_keys=set())
            path.write_bytes(b"done")

    artifact = asyncio.run(
        store.download_message_media(
            SlowMessage(),
            file_name="photo.jpg",
            mime_type="image/jpeg",
            media_kind=MediaKind.PHOTO,
            sequence=1,
        )
    )

    assert observed["removed"] == []
    assert store.path_for(artifact).read_bytes() == b"done"


def test_media_store_release_policy_and_part_cleanup(tmp_path):
    store = MediaStore(tmp_path)
    artifact = store.write_bytes(b"abc", file_name="a.jpg", mime_type="image/jpeg", media_kind=MediaKind.PHOTO)

    assert not store.release_for_dispatch(artifact, "pending")
    assert store.path_for(artifact).exists()
    assert store.release_for_dispatch(artifact, "sent")
    assert not store.path_for(artifact).exists()

    stale = tmp_path / "stale.part"
    stale.write_bytes(b"old")
    active = tmp_path / "active.part"
    active.write_bytes(b"new")
    handle = active.open("r+b")
    try:
        store.lock_part_for_test(handle)
        removed = store.cleanup_unreferenced_parts(referenced_keys=set())
    finally:
        store.unlock_part_for_test(handle)
        handle.close()

    assert stale.name in removed
    assert active.exists()


def test_compose_mounts_named_media_volume_for_runtime_roles():
    compose = Path("docker-compose.server.yml").read_text()
    target = "/var/lib/tg-v-chat/media"

    assert "tg-v-chat-media:" in compose
    assert compose.count("source: tg-v-chat-media") == 3
    assert compose.count("target: ${TG_V_CHAT_MEDIA_ROOT:?TG_V_CHAT_MEDIA_ROOT is required}") == 3
    assert os.path.isabs(target)
    assert f"TG_V_CHAT_MEDIA_ROOT={target}" in Path(".env.example").read_text()
