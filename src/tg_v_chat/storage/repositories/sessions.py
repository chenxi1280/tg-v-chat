from __future__ import annotations

from tg_v_chat.domain import DeveloperSlot, SessionStatus
from tg_v_chat.storage.models import TgSessionSlotModel, utc_now


class SessionSlotRepository:
    def __init__(self, session):
        self._session = session

    def create(
        self,
        account_id: int,
        *,
        slot: DeveloperSlot,
        encrypted_session: str | None,
        status: SessionStatus,
    ):
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

    def get_for_account(self, account_id: int, slot: DeveloperSlot) -> TgSessionSlotModel | None:
        return (
            self._session.query(TgSessionSlotModel)
            .filter_by(bound_tg_account_id=account_id, developer_slot=slot.value)
            .one_or_none()
        )

    def get(self, slot_id: int) -> TgSessionSlotModel:
        return self._required(slot_id)

    def list_authorized(self) -> list[TgSessionSlotModel]:
        statuses = (SessionStatus.ACTIVE.value, SessionStatus.STANDBY.value)
        return (
            self._session.query(TgSessionSlotModel)
            .filter(TgSessionSlotModel.status.in_(statuses))
            .filter(TgSessionSlotModel.encrypted_session.isnot(None))
            .order_by(TgSessionSlotModel.bound_tg_account_id.asc(), TgSessionSlotModel.id.asc())
            .all()
        )

    def authorize_or_replace(
        self,
        account_id: int,
        slot: DeveloperSlot,
        *,
        encrypted_session: str,
        status: SessionStatus,
    ) -> TgSessionSlotModel:
        _validate_usable_status(slot, status)
        model = self.get_for_account(account_id, slot)
        if model is None:
            model = TgSessionSlotModel(bound_tg_account_id=account_id, developer_slot=slot.value)
            self._session.add(model)
            model.revision = 0
        model.encrypted_session = encrypted_session
        model.status = status.value
        model.failure_code = None
        model.failure_reason = None
        model.updated_at = utc_now()
        model.revision += 1
        self._session.flush()
        return model

    def mark_failed(self, slot_id: int, reason: str, *, code: str | None = None) -> None:
        model = self._required(slot_id)
        model.status = SessionStatus.FAILED.value
        model.failure_code = code
        model.failure_reason = reason
        model.last_checked_at = utc_now()
        model.updated_at = utc_now()
        self._session.flush()

    def mark_healthy(self, slot_id: int, status: SessionStatus) -> TgSessionSlotModel:
        model = self._required(slot_id)
        _validate_usable_status(DeveloperSlot(model.developer_slot), status)
        checked_at = utc_now()
        model.status = status.value
        model.failure_code = None
        model.failure_reason = None
        model.last_checked_at = checked_at
        model.last_healthy_at = checked_at
        model.updated_at = checked_at
        self._session.flush()
        return model

    def mark_revoked(self, slot_id: int, reason: str) -> TgSessionSlotModel:
        model = self._required(slot_id)
        model.status = SessionStatus.REVOKED.value
        model.failure_code = "unauthorized"
        model.failure_reason = reason
        model.last_checked_at = utc_now()
        model.updated_at = utc_now()
        self._session.flush()
        return model

    def delete_for_account(self, account_id: int) -> None:
        for slot in self.list_for_account(account_id):
            self._session.delete(slot)
        self._session.flush()

    def _required(self, slot_id: int) -> TgSessionSlotModel:
        model = self._session.get(TgSessionSlotModel, slot_id)
        if not model:
            raise LookupError(f"TgSessionSlot 不存在: {slot_id}")
        return model


def _validate_usable_status(slot: DeveloperSlot, status: SessionStatus) -> None:
    expected = SessionStatus.ACTIVE if slot is DeveloperSlot.PRIMARY else SessionStatus.STANDBY
    if status is not expected:
        raise ValueError(f"slot 状态不匹配: {slot.value} 只能写入 {expected.value}")
