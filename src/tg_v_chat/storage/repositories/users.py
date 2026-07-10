from __future__ import annotations

from tg_v_chat.storage.models import SystemUserModel


class UserRepository:
    def __init__(self, session):
        self._session = session

    def get_or_create(self, telegram_user_id: int) -> SystemUserModel:
        user = self._session.query(SystemUserModel).filter_by(telegram_user_id=telegram_user_id).one_or_none()
        if user:
            return user
        user = SystemUserModel(telegram_user_id=telegram_user_id)
        self._session.add(user)
        self._session.flush()
        return user

    def get(self, user_id: int) -> SystemUserModel:
        user = self._session.get(SystemUserModel, user_id)
        if not user:
            raise LookupError(f"SystemUser 不存在: {user_id}")
        return user

    def get_by_telegram_id(self, telegram_user_id: int) -> SystemUserModel:
        user = self._session.query(SystemUserModel).filter_by(telegram_user_id=telegram_user_id).one_or_none()
        if not user:
            raise LookupError(f"SystemUser 不存在: {telegram_user_id}")
        return user

    def mark_disabled(self, user_id: int) -> SystemUserModel:
        user = self.get(user_id)
        user.status = "disabled"
        self._session.flush()
        return user
