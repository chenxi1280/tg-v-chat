from __future__ import annotations

from tg_v_chat.storage.models import BoundTgAccountModel, TgSessionSlotModel


class AccountRepository:
    def __init__(self, session):
        self._session = session

    def count_for_user(self, system_user_id: int) -> int:
        return (
            self._session.query(BoundTgAccountModel)
            .filter(BoundTgAccountModel.system_user_id == system_user_id)
            .filter(BoundTgAccountModel.status.notin_(("disabled", "deleted")))
            .count()
        )

    def list_for_user(self, system_user_id: int) -> list[BoundTgAccountModel]:
        return (
            self._session.query(BoundTgAccountModel)
            .filter(BoundTgAccountModel.system_user_id == system_user_id)
            .filter(BoundTgAccountModel.status != "deleted")
            .order_by(BoundTgAccountModel.id.asc())
            .all()
        )

    def list_by_status_for_user(self, system_user_id: int, status: str) -> list[BoundTgAccountModel]:
        return (
            self._session.query(BoundTgAccountModel)
            .filter_by(system_user_id=system_user_id, status=status)
            .order_by(BoundTgAccountModel.id.asc())
            .all()
        )

    def list_active(self) -> list[BoundTgAccountModel]:
        return (
            self._session.query(BoundTgAccountModel)
            .filter_by(status="active")
            .order_by(BoundTgAccountModel.id.asc())
            .all()
        )

    def list_operational(self) -> list[BoundTgAccountModel]:
        return (
            self._session.query(BoundTgAccountModel)
            .filter(BoundTgAccountModel.status.in_(("active", "degraded")))
            .order_by(BoundTgAccountModel.id.asc())
            .all()
        )

    def find_incomplete_for_user_phone(self, system_user_id: int, phone_number: str) -> BoundTgAccountModel | None:
        accounts = (
            self._session.query(BoundTgAccountModel)
            .filter_by(system_user_id=system_user_id, phone_number=phone_number)
            .filter(BoundTgAccountModel.status.notin_(("active", "deleted")))
            .order_by(BoundTgAccountModel.id.asc())
            .all()
        )
        return next((account for account in accounts if not self._has_sessions(account.id)), None)

    def create(self, system_user_id: int, phone_number: str) -> BoundTgAccountModel:
        account = BoundTgAccountModel(system_user_id=system_user_id, phone_number=phone_number)
        self._session.add(account)
        self._session.flush()
        return account

    def update_profile(
        self,
        account_id: int,
        *,
        display_name: str | None,
        username: str | None,
    ) -> BoundTgAccountModel:
        account = self.get(account_id)
        account.display_name = _clean_text(display_name)
        account.username = _clean_username(username)
        self._session.flush()
        return account

    def get(self, account_id: int) -> BoundTgAccountModel:
        account = self._session.get(BoundTgAccountModel, account_id)
        if not account:
            raise LookupError(f"BoundTgAccount 不存在: {account_id}")
        return account

    def get_for_user(self, account_id: int, system_user_id: int) -> BoundTgAccountModel:
        account = self._session.get(BoundTgAccountModel, account_id)
        if not account or account.system_user_id != system_user_id:
            raise LookupError(f"BoundTgAccount 不存在: {account_id}")
        return account

    def mark_active(self, account_id: int) -> BoundTgAccountModel:
        account = self.get(account_id)
        account.status = "active"
        self._session.flush()
        return account

    def mark_binding(self, account_id: int) -> BoundTgAccountModel:
        account = self.get(account_id)
        account.status = "binding"
        self._session.flush()
        return account

    def mark_degraded(self, account_id: int) -> BoundTgAccountModel:
        return self._mark_status(account_id, "degraded")

    def mark_reauth_required(self, account_id: int) -> BoundTgAccountModel:
        return self._mark_status(account_id, "reauth_required")

    def mark_disabled(self, account_id: int) -> BoundTgAccountModel:
        account = self.get(account_id)
        account.status = "disabled"
        self._session.flush()
        return account

    def mark_deleted(self, account_id: int) -> BoundTgAccountModel:
        account = self.get(account_id)
        account.status = "deleted"
        self._session.flush()
        return account

    def delete(self, account_id: int) -> None:
        account = self.get(account_id)
        self._session.delete(account)
        self._session.flush()

    def _mark_status(self, account_id: int, status: str) -> BoundTgAccountModel:
        account = self.get(account_id)
        account.status = status
        self._session.flush()
        return account

    def _has_sessions(self, account_id: int) -> bool:
        return self._session.query(TgSessionSlotModel.id).filter_by(bound_tg_account_id=account_id).first() is not None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_username(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return cleaned[1:] if cleaned.startswith("@") else cleaned
