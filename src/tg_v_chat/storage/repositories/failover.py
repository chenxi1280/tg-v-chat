from __future__ import annotations

from tg_v_chat.domain import DeveloperSlot
from tg_v_chat.storage.models import SessionFailoverEventModel


class FailoverRepository:
    def __init__(self, session):
        self._session = session

    def create(self, account_id: int, *, from_slot: DeveloperSlot, to_slot: DeveloperSlot, reason: str):
        model = SessionFailoverEventModel(
            bound_tg_account_id=account_id,
            from_slot=from_slot.value,
            to_slot=to_slot.value,
            status="switched",
            reason=reason,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def create_exhausted(self, account_id: int, from_slot: DeveloperSlot, reason: str):
        model = SessionFailoverEventModel(
            bound_tg_account_id=account_id,
            from_slot=from_slot.value,
            to_slot=from_slot.value,
            status="exhausted",
            reason=reason,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def list_for_account(self, account_id: int) -> list[SessionFailoverEventModel]:
        return self._session.query(SessionFailoverEventModel).filter_by(bound_tg_account_id=account_id).all()
