from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeveloperSlot, MAX_BOUND_ACCOUNTS, SessionStatus


class AuthStep(str, Enum):
    CODE_REQUIRED = "code_required"
    PASSWORD_REQUIRED = "password_required"
    COMPLETE = "complete"


class AuthFailure(RuntimeError):
    def __init__(self, message: str, *, restart_required: bool = False):
        super().__init__(message)
        self.message = message
        self.restart_required = restart_required


@dataclass(frozen=True)
class AuthChallenge:
    phone_number: str
    developer_slot: DeveloperSlot
    phone_code_hash: str
    pending_session: str | None = None
    id: int | None = None
    bound_tg_account_id: int | None = None


@dataclass(frozen=True)
class PasswordRequired:
    pending_session: str


class TelegramAuthenticator(Protocol):
    def start(self, phone_number: str, slot: DeveloperSlot) -> AuthChallenge:
        raise NotImplementedError

    def complete_code(self, challenge: AuthChallenge, code: str) -> str | AuthStep | PasswordRequired:
        raise NotImplementedError

    def complete_password(self, challenge: AuthChallenge, password: str) -> str:
        raise NotImplementedError


class AuthService:
    def __init__(self, uow, authenticator: TelegramAuthenticator, cipher: SessionCipher):
        self._uow = uow
        self._authenticator = authenticator
        self._cipher = cipher

    def start_binding(self, telegram_user_id: int, phone_number: str, slot: DeveloperSlot) -> AuthChallenge:
        user = self._uow.users.get_or_create(telegram_user_id)
        account = self._uow.accounts.find_incomplete_for_user_phone(user.id, phone_number)
        if account is None:
            if self._uow.accounts.count_for_user(user.id) >= MAX_BOUND_ACCOUNTS:
                raise ValueError("每个系统用户最多绑定 20 个 Telegram 账号")
            account = self._uow.accounts.create(user.id, phone_number)
        else:
            self._uow.auth_challenges.delete_for_account(account.id)
            account = self._uow.accounts.mark_binding(account.id)
        external = self._authenticator.start(phone_number, slot)
        pending_session = _encrypt_optional(self._cipher, external.pending_session)
        challenge = self._uow.auth_challenges.create(
            account.id,
            phone_number,
            slot,
            external.phone_code_hash,
            pending_session=pending_session,
        )
        self._uow.commit()
        return _challenge_from_model(challenge)

    def submit_code(self, challenge_id: int, code: str) -> AuthStep:
        model = self._uow.auth_challenges.get(challenge_id)
        challenge = _challenge_from_model(model, self._cipher, include_pending=True)
        result = self._authenticator.complete_code(challenge, code)
        if isinstance(result, PasswordRequired):
            model.pending_session = self._cipher.encrypt(result.pending_session)
            model.status = AuthStep.PASSWORD_REQUIRED.value
            self._uow.commit()
            return AuthStep.PASSWORD_REQUIRED
        if result is AuthStep.PASSWORD_REQUIRED:
            model.status = AuthStep.PASSWORD_REQUIRED.value
            self._uow.commit()
            return AuthStep.PASSWORD_REQUIRED
        model.status = AuthStep.COMPLETE.value
        self._activate_account(model.bound_tg_account_id, DeveloperSlot(model.developer_slot), str(result))
        return AuthStep.COMPLETE

    def submit_password(self, challenge_id: int, password: str):
        model = self._uow.auth_challenges.get(challenge_id)
        challenge = _challenge_from_model(model, self._cipher, include_pending=True)
        session_value = self._authenticator.complete_password(challenge, password)
        model.status = AuthStep.COMPLETE.value
        return self._activate_account(model.bound_tg_account_id, DeveloperSlot(model.developer_slot), session_value)

    def _activate_account(self, account_id: int, active_slot: DeveloperSlot, session_value: str):
        encrypted = self._cipher.encrypt(session_value)
        for slot in DeveloperSlot:
            self._create_auth_slot(account_id, active_slot, encrypted, slot)
        account = self._uow.accounts.mark_active(account_id)
        self._uow.commit()
        return account

    def _create_auth_slot(
        self,
        account_id: int,
        active_slot: DeveloperSlot,
        encrypted: str,
        slot: DeveloperSlot,
    ) -> None:
        status = SessionStatus.ACTIVE if slot is active_slot else SessionStatus.EXPIRED
        session_value = encrypted if slot is active_slot else None
        self._uow.sessions.create(account_id, slot, session_value, status)


def _challenge_from_model(
    model,
    cipher: SessionCipher | None = None,
    *,
    include_pending: bool = False,
) -> AuthChallenge:
    return AuthChallenge(
        phone_number=model.phone_number,
        developer_slot=DeveloperSlot(model.developer_slot),
        phone_code_hash=model.phone_code_hash,
        pending_session=_pending_session(model, cipher, include_pending),
        id=model.id,
        bound_tg_account_id=model.bound_tg_account_id,
    )


def _encrypt_optional(cipher: SessionCipher, value: str | None) -> str | None:
    if value is None:
        return None
    return cipher.encrypt(value)


def _decrypt_optional(cipher: SessionCipher | None, token: str | None) -> str | None:
    if token is None:
        return None
    if cipher is None:
        raise ValueError("pending session cipher is required")
    return cipher.decrypt(token)


def _pending_session(model, cipher: SessionCipher | None, include_pending: bool) -> str | None:
    if not include_pending:
        return None
    return _decrypt_optional(cipher, model.pending_session)
