from __future__ import annotations

from tg_v_chat.storage.models import BotConversationStateModel


class ConversationStateRepository:
    def __init__(self, session):
        self._session = session

    def get(self, system_user_id: int) -> BotConversationStateModel | None:
        return self._session.query(BotConversationStateModel).filter_by(system_user_id=system_user_id).one_or_none()

    def set(self, system_user_id: int, state: str, challenge_id: int | None = None) -> BotConversationStateModel:
        existing = self.get(system_user_id)
        if existing:
            existing.state = state
            existing.auth_challenge_id = challenge_id
            self._session.flush()
            return existing
        model = BotConversationStateModel(
            system_user_id=system_user_id,
            state=state,
            auth_challenge_id=challenge_id,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def clear(self, system_user_id: int) -> None:
        existing = self.get(system_user_id)
        if existing:
            self._session.delete(existing)
            self._session.flush()
