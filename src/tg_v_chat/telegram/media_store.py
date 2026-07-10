from __future__ import annotations

import fcntl
import os
from pathlib import Path
from uuid import uuid4

from tg_v_chat.domain import MediaArtifact, MediaKind


ROOT_MODE = 0o700
FILE_MODE = 0o600
PART_SUFFIX = ".part"
RELEASABLE_DISPATCH_STATUSES = frozenset({"sent", "failed"})


class MediaStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise ValueError("TG_V_CHAT_MEDIA_ROOT must be absolute")
        self.root.mkdir(mode=ROOT_MODE, parents=True, exist_ok=True)
        os.chmod(self.root, ROOT_MODE)

    def write_bytes(
        self,
        data: bytes,
        *,
        file_name: str,
        mime_type: str,
        media_kind: MediaKind,
        sequence: int = 1,
    ) -> MediaArtifact:
        key = _new_storage_key()
        part = self.part_path_for(key)
        part.write_bytes(data)
        os.chmod(part, FILE_MODE)
        final = self.final_path_for(key)
        os.replace(part, final)
        os.chmod(final, FILE_MODE)
        return self._artifact(
            key,
            file_name=file_name,
            mime_type=mime_type,
            byte_size=len(data),
            media_kind=media_kind,
            sequence=sequence,
        )

    async def download_message_media(
        self,
        message,
        *,
        file_name: str,
        mime_type: str,
        media_kind: MediaKind,
        sequence: int = 1,
    ) -> MediaArtifact:
        key = _new_storage_key()
        part = self.part_path_for(key)
        try:
            with part.open("a+b") as handle:
                os.chmod(part, FILE_MODE)
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                await message.download_media(file=str(part))
                final = self.final_path_for(key)
                os.replace(part, final)
                os.chmod(final, FILE_MODE)
                size = final.stat().st_size
                return self._artifact(
                    key,
                    file_name=file_name,
                    mime_type=mime_type,
                    byte_size=size,
                    media_kind=media_kind,
                    sequence=sequence,
                )
        except Exception:
            part.unlink(missing_ok=True)
            raise

    def path_for(self, artifact: MediaArtifact) -> Path:
        return self.final_path_for(artifact.storage_key)

    def final_path_for(self, storage_key: str) -> Path:
        return self.root / _safe_storage_key(storage_key)

    def part_path_for(self, storage_key: str) -> Path:
        return self.root / f"{_safe_storage_key(storage_key)}{PART_SUFFIX}"

    def release_for_dispatch(self, artifact: MediaArtifact, dispatch_status: str) -> bool:
        if dispatch_status not in RELEASABLE_DISPATCH_STATUSES:
            return False
        self.path_for(artifact).unlink(missing_ok=True)
        return True

    def discard(self, artifact: MediaArtifact) -> None:
        self.path_for(artifact).unlink(missing_ok=True)
        self.part_path_for(artifact.storage_key).unlink(missing_ok=True)

    def cleanup_unreferenced_parts(self, *, referenced_keys: set[str]) -> list[str]:
        removed = []
        for path in self.root.glob(f"*{PART_SUFFIX}"):
            key = path.name.removesuffix(PART_SUFFIX)
            if key in referenced_keys or not _remove_if_lockable_part(path):
                continue
            removed.append(path.name)
        return removed

    def lock_part_for_test(self, handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock_part_for_test(self, handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _artifact(
        self,
        key: str,
        *,
        file_name: str,
        mime_type: str,
        byte_size: int,
        media_kind: MediaKind,
        sequence: int,
    ) -> MediaArtifact:
        return MediaArtifact(key, _sanitize_file_name(file_name), mime_type, byte_size, media_kind, sequence)


def _remove_if_lockable_part(path: Path) -> bool:
    try:
        with path.open("r+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            path.unlink(missing_ok=True)
            return True
    except BlockingIOError:
        return False
    except FileNotFoundError:
        return False


def _sanitize_file_name(value: str) -> str:
    cleaned = value.replace("/", "_").replace("\\", "_").strip("._ ")
    return cleaned or "telegram-media"


def _new_storage_key() -> str:
    return uuid4().hex


def _safe_storage_key(storage_key: str) -> str:
    if "/" in storage_key or "\\" in storage_key or storage_key in {"", ".", ".."}:
        raise ValueError("invalid media storage key")
    return storage_key
