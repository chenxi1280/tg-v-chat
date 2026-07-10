from __future__ import annotations

from tg_v_chat.domain import DeveloperSlot
from tg_v_chat.storage.models import AuthChallengeModel


class AuthChallengeRepository:
    def __init__(self, session):
        self._session = session

    def create(
        self,
        account_id: int,
        *,
        phone: str,
        slot: DeveloperSlot,
        code_hash: str,
        pending_session: str | None = None,
        purpose: str = "initial_bind",
    ):
        model = AuthChallengeModel(
            bound_tg_account_id=account_id,
            phone_number=phone,
            developer_slot=slot.value,
            phone_code_hash=code_hash,
            pending_session=pending_session,
            purpose=purpose,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get(self, challenge_id: int) -> AuthChallengeModel:
        challenge = self._session.get(AuthChallengeModel, challenge_id)
        if not challenge:
            raise LookupError(f"AuthChallenge 不存在: {challenge_id}")
        return challenge

    def mark_status(self, challenge_id: int, status: str) -> AuthChallengeModel:
        challenge = self.get(challenge_id)
        challenge.status = status
        self._session.flush()
        return challenge

    def list_for_account(self, account_id: int) -> list[AuthChallengeModel]:
        return (
            self._session.query(AuthChallengeModel)
            .filter_by(bound_tg_account_id=account_id)
            .order_by(AuthChallengeModel.id.asc())
            .all()
        )

    def delete_for_account(self, account_id: int) -> None:
        for challenge in self.list_for_account(account_id):
            self._session.delete(challenge)
        self._session.flush()
