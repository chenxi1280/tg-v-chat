from __future__ import annotations

from tg_v_chat.domain import DeveloperSlot, SessionStatus
from tg_v_chat.storage.models import TgSessionSlotModel


class SessionSlotRepository:
    def __init__(self, session):
        self._session = session

    def create(self, account_id: int, slot: DeveloperSlot, encrypted_session: str, status: SessionStatus):
        model = TgSessionSlotModel(
            bound_tg_account_id=account_id,
            developer_slot=slot.value,
            encrypted_session=encrypted_session,
            status=status.value,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def list_for_account(self, account_id: int) -> list[TgSessionSlotModel]:
        rows = self._session.query(TgSessionSlotModel).filter_by(bound_tg_account_id=account_id).all()
        order = {slot.value: index for index, slot in enumerate(DeveloperSlot)}
        return sorted(rows, key=lambda row: order[row.developer_slot])

    def mark_failed(self, slot_id: int, reason: str) -> None:
        model = self._session.get(TgSessionSlotModel, slot_id)
        if not model:
            raise LookupError(f"TgSessionSlot 不存在: {slot_id}")
        model.status = SessionStatus.FAILED.value
        model.failure_reason = reason
        self._session.flush()

    def delete_for_account(self, account_id: int) -> None:
        for slot in self.list_for_account(account_id):
            self._session.delete(slot)
        self._session.flush()
