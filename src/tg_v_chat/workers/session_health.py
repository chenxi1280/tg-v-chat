from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeliveryUncertain, DeveloperSlot, MediaArtifact, MediaKind, SessionSlotRef, SessionStatus
from tg_v_chat.services.auth import recompute_account_status
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.telethon_clients.helpers import _run_async


class SessionVerifier(Protocol):
    def verify(self, slot: SessionSlotRef, session_string: str) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class SessionHealthWorker:
    session_factory: object
    cipher: SessionCipher
    verifier: SessionVerifier
    media_store: object | None = None

    def run_once(self) -> None:
        self._verify_sessions()
        self._release_terminal_media()

    def _verify_sessions(self) -> None:
        with UnitOfWork(self.session_factory) as uow:
            account_ids = set()
            for row in uow.sessions.list_authorized():
                account_ids.add(row.bound_tg_account_id)
                self._verify_slot(uow, row)
            for account_id in account_ids:
                recompute_account_status(uow, account_id)
            uow.commit()

    def _verify_slot(self, uow, row) -> None:
        try:
            ok = self.verifier.verify(_slot_ref(row), self.cipher.decrypt(row.encrypted_session))
        except DeliveryUncertain:
            return
        if not ok:
            uow.sessions.mark_revoked(row.id, "session unauthorized")
            return
        uow.sessions.mark_healthy(row.id, SessionStatus(row.status))

    def _release_terminal_media(self) -> None:
        if self.media_store is None:
            return
        with UnitOfWork(self.session_factory) as uow:
            for row in uow.media_artifacts.list_releasable():
                artifact = _artifact_from_row(row)
                if self.media_store.release_for_dispatch(artifact, row.status):
                    uow.media_artifacts.mark_released(row.storage_key)
            uow.commit()


class TelethonSessionVerifier:
    def __init__(self, app_configs: dict[DeveloperSlot, object]):
        self._app_configs = app_configs

    def verify(self, slot: SessionSlotRef, session_string: str) -> bool:
        return _run_async(self._verify(slot, session_string))

    async def _verify(self, slot: SessionSlotRef, session_string: str) -> bool:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        app_config = self._app_configs[slot.developer_slot]
        client = TelegramClient(StringSession(session_string), app_config.api_id, app_config.api_hash)
        try:
            await client.connect()
            return bool(await client.is_user_authorized())
        except (ConnectionError, OSError, TimeoutError) as exc:
            raise DeliveryUncertain("transport_unknown", f"session probe failed: {exc}") from exc
        finally:
            await client.disconnect()


def _slot_ref(row) -> SessionSlotRef:
    return SessionSlotRef(
        id=row.id,
        bound_tg_account_id=row.bound_tg_account_id,
        developer_slot=DeveloperSlot(row.developer_slot),
        encrypted_session=row.encrypted_session,
    )


def _artifact_from_row(row) -> MediaArtifact:
    return MediaArtifact(
        storage_key=row.storage_key,
        file_name=row.file_name,
        mime_type=row.mime_type,
        byte_size=row.byte_size,
        media_kind=MediaKind(row.media_kind),
        sequence=row.sequence,
        metadata_json=row.metadata_json,
    )
