from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeveloperSlot, MAX_BOUND_ACCOUNTS, SessionStatus


ACTIVE_CHALLENGE_STATUSES = {"code_required", "password_required"}
AUTHORIZABLE_ACCOUNT_STATUSES = {"binding", "active", "degraded", "reauth_required"}
SLOT_ACTIONS = {"bind", "reauth"}


class AuthStep(str, Enum):
    CODE_REQUIRED = "code_required"
    PASSWORD_REQUIRED = "password_required"
    COMPLETE = "complete"


class AuthPurpose(str, Enum):
    INITIAL_BIND = "initial_bind"
    SLOT_AUTHORIZATION = "slot_authorization"


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
    purpose: AuthPurpose = AuthPurpose.INITIAL_BIND


@dataclass(frozen=True)
class AuthenticatedSession:
    session_string: str
    telegram_user_id: int
    display_name: str | None
    username: str | None


@dataclass(frozen=True)
class PasswordRequired:
    pending_session: str


class TelegramAuthenticator(Protocol):
    def start(self, phone_number: str, slot: DeveloperSlot) -> AuthChallenge:
        raise NotImplementedError

    def complete_code(self, challenge: AuthChallenge, code: str) -> AuthenticatedSession | PasswordRequired:
        raise NotImplementedError

    def complete_password(self, challenge: AuthChallenge, password: str) -> AuthenticatedSession:
        raise NotImplementedError


class AuthService:
    def __init__(self, uow, authenticator: TelegramAuthenticator, cipher: SessionCipher):
        self._uow = uow
        self._authenticator = authenticator
        self._cipher = cipher

    def start_binding(self, telegram_user_id: int, phone_number: str, slot: DeveloperSlot) -> AuthChallenge:
        if slot is not DeveloperSlot.PRIMARY:
            raise ValueError("首次绑定只能授权 primary 槽位")
        user = self._uow.users.get_or_create(telegram_user_id)
        with self._uow.user_locks.acquire(_user_lock_key(user.id)):
            account, created = self._binding_account(user.id, phone_number)
            with self._user_account_locks(user.id):
                if not created:
                    self._uow.auth_challenges.delete_for_account(account.id)
                    account = self._uow.accounts.mark_binding(account.id)
                self._refresh_authorizable_account(account)
                self._cancel_user_active_challenges(user.id)
                challenge = self._start_challenge(account, slot, AuthPurpose.INITIAL_BIND)
                self._uow.commit()
                return _challenge_from_model(challenge)

    def start_slot_authorization(
        self,
        telegram_user_id: int,
        account_id: int,
        slot: DeveloperSlot,
        *,
        action: str,
    ) -> AuthChallenge:
        user = self._uow.users.get_or_create(telegram_user_id)
        with self._uow.user_locks.acquire(_user_lock_key(user.id)):
            account = self._uow.accounts.get_for_user(account_id, user.id)
            with self._user_account_locks(user.id):
                self._refresh_authorizable_account(account)
                self._validate_slot_action(account.id, slot, action)
                challenge = self._active_slot_challenge(account.id, slot)
                if challenge is None:
                    self._cancel_user_active_challenges(user.id)
                    challenge = self._start_challenge(account, slot, AuthPurpose.SLOT_AUTHORIZATION)
                else:
                    self._cancel_user_active_challenges(user.id, keep_challenge_id=challenge.id)
                self._uow.commit()
                return _challenge_from_model(challenge)

    def restart_challenge(self, telegram_user_id: int, challenge_id: int) -> AuthChallenge:
        user = self._uow.users.get_or_create(telegram_user_id)
        with self._uow.user_locks.acquire(_user_lock_key(user.id)):
            existing = self._uow.auth_challenges.get(challenge_id)
            account = self._uow.accounts.get_for_user(existing.bound_tg_account_id, user.id)
            with self._user_account_locks(user.id):
                self._uow.session.refresh(existing)
                self._refresh_authorizable_account(account)
                if existing.status != AuthStep.COMPLETE.value:
                    self._uow.auth_challenges.mark_status(existing.id, "expired")
                self._cancel_user_active_challenges(user.id)
                challenge = self._restart_locked_challenge(account, existing)
                self._uow.commit()
                return _challenge_from_model(challenge)

    def _binding_account(self, user_id: int, phone_number: str):
        account = self._uow.accounts.find_incomplete_for_user_phone(user_id, phone_number)
        if account is not None:
            return account, False
        if self._uow.accounts.count_for_user(user_id) >= MAX_BOUND_ACCOUNTS:
            raise ValueError("每个系统用户最多绑定 20 个 Telegram 账号")
        return self._uow.accounts.create(user_id, phone_number), True

    def _user_account_locks(self, user_id: int):
        stack = ExitStack()
        for account in self._uow.accounts.list_for_user(user_id):
            stack.enter_context(self._uow.account_locks.acquire(account.id))
        return stack

    def _refresh_authorizable_account(self, account):
        self._uow.session.refresh(account)
        if account.status not in AUTHORIZABLE_ACCOUNT_STATUSES:
            raise ValueError("账号状态不允许继续授权")
        return account

    def _validate_slot_action(self, account_id: int, slot: DeveloperSlot, action: str) -> None:
        if action not in SLOT_ACTIONS:
            raise ValueError(f"未知槽位授权动作: {action}")
        session = self._uow.sessions.get_for_account(account_id, slot)
        authorized = bool(session and session.encrypted_session)
        if (action == "bind") == authorized:
            raise ValueError(f"槽位授权动作不匹配: {slot.value} 应使用 {'reauth' if authorized else 'bind'}")

    def _active_slot_challenge(self, account_id: int, slot: DeveloperSlot):
        matches = [
            row
            for row in self._uow.auth_challenges.list_for_account(account_id)
            if row.developer_slot == slot.value
            and row.purpose == AuthPurpose.SLOT_AUTHORIZATION.value
            and row.status in ACTIVE_CHALLENGE_STATUSES
        ]
        for stale in matches[:-1]:
            self._uow.auth_challenges.mark_status(stale.id, "cancelled")
        return matches[-1] if matches else None

    def _cancel_user_active_challenges(self, user_id: int, keep_challenge_id: int | None = None) -> None:
        for account in self._uow.accounts.list_for_user(user_id):
            for challenge in self._uow.auth_challenges.list_for_account(account.id):
                if challenge.id == keep_challenge_id:
                    continue
                if challenge.status in ACTIVE_CHALLENGE_STATUSES:
                    self._uow.auth_challenges.mark_status(challenge.id, "cancelled")

    def _restart_locked_challenge(self, account, existing):
        purpose = AuthPurpose(existing.purpose)
        slot = DeveloperSlot(existing.developer_slot)
        if purpose is AuthPurpose.INITIAL_BIND:
            self._uow.auth_challenges.delete_for_account(account.id)
            return self._start_challenge(account, DeveloperSlot.PRIMARY, purpose)
        action = self._slot_action_for_current_session(account.id, slot)
        self._validate_slot_action(account.id, slot, action)
        return self._start_challenge(account, slot, purpose)

    def _slot_action_for_current_session(self, account_id: int, slot: DeveloperSlot) -> str:
        session = self._uow.sessions.get_for_account(account_id, slot)
        return "reauth" if session is not None and session.encrypted_session else "bind"

    def _start_challenge(self, account, slot: DeveloperSlot, purpose: AuthPurpose):
        external = self._authenticator.start(account.phone_number, slot)
        return self._uow.auth_challenges.create(
            account.id,
            phone=account.phone_number,
            slot=slot,
            code_hash=external.phone_code_hash,
            pending_session=_encrypt_optional(self._cipher, external.pending_session),
            purpose=purpose.value,
        )

    def submit_code(self, challenge_id: int, code: str) -> AuthStep:
        model = self._uow.auth_challenges.get(challenge_id)
        with self._uow.account_locks.acquire(model.bound_tg_account_id):
            step = self._refresh_challenge_account(model, AuthStep.CODE_REQUIRED)
            if step is not None:
                return step
            challenge = _challenge_from_model(model, self._cipher, include_pending=True)
            result = self._authenticator.complete_code(challenge, code)
            step = self._refresh_challenge_account(model, AuthStep.CODE_REQUIRED)
            if step is not None:
                return step
            if isinstance(result, PasswordRequired):
                model.pending_session = self._cipher.encrypt(result.pending_session)
                model.status = AuthStep.PASSWORD_REQUIRED.value
                self._uow.commit()
                return AuthStep.PASSWORD_REQUIRED
            model.status = AuthStep.COMPLETE.value
            self._complete_authorization(model, result)
            return AuthStep.COMPLETE

    def submit_password(self, challenge_id: int, password: str):
        model = self._uow.auth_challenges.get(challenge_id)
        with self._uow.account_locks.acquire(model.bound_tg_account_id):
            account = self._refresh_challenge_account(model, AuthStep.PASSWORD_REQUIRED)
            if account is not None:
                return account
            challenge = _challenge_from_model(model, self._cipher, include_pending=True)
            auth_session = self._authenticator.complete_password(challenge, password)
            account = self._refresh_challenge_account(model, AuthStep.PASSWORD_REQUIRED)
            if account is not None:
                return account
            model.status = AuthStep.COMPLETE.value
            return self._complete_authorization(model, auth_session)

    def _refresh_challenge_account(self, challenge, expected_step: AuthStep):
        self._uow.session.refresh(challenge)
        account = self._uow.accounts.get(challenge.bound_tg_account_id)
        self._refresh_authorizable_account(account)
        if challenge.status == AuthStep.COMPLETE.value:
            return account if expected_step is AuthStep.PASSWORD_REQUIRED else AuthStep.COMPLETE
        if expected_step is AuthStep.CODE_REQUIRED and challenge.status == AuthStep.PASSWORD_REQUIRED.value:
            return AuthStep.PASSWORD_REQUIRED
        if challenge.status != expected_step.value:
            raise ValueError(f"认证挑战状态不允许继续: {challenge.status}")
        return None

    def _complete_authorization(self, challenge, auth_session: AuthenticatedSession):
        account_id = challenge.bound_tg_account_id
        active_slot = DeveloperSlot(challenge.developer_slot)
        encrypted = self._cipher.encrypt(auth_session.session_string)
        status = SessionStatus.ACTIVE if active_slot is DeveloperSlot.PRIMARY else SessionStatus.STANDBY
        identity = _required_telegram_identity(auth_session.telegram_user_id)
        with self._uow.telegram_identity_locks.acquire(identity):
            self._require_available_telegram_identity(account_id, identity)
            self._uow.accounts.update_telegram_identity(account_id, identity)
            self._uow.sessions.authorize_or_replace(account_id, active_slot, encrypted_session=encrypted, status=status)
            if challenge.purpose == AuthPurpose.INITIAL_BIND.value:
                self._create_initial_placeholders(account_id)
            self._uow.accounts.update_profile(
                account_id,
                display_name=auth_session.display_name,
                username=auth_session.username,
            )
            account = recompute_account_status(self._uow, account_id)
            self._uow.commit()
            return account

    def _require_available_telegram_identity(self, account_id: int, telegram_user_id: int) -> None:
        existing = self._uow.accounts.get_by_telegram_identity(telegram_user_id)
        if existing is not None and existing.id != account_id:
            raise AuthFailure("bound_account_already_bound: 该 Telegram 账号已绑定到其他账号。", restart_required=True)

    def _create_initial_placeholders(self, account_id: int) -> None:
        for slot in (DeveloperSlot.STANDBY_1, DeveloperSlot.STANDBY_2):
            if self._uow.sessions.get_for_account(account_id, slot) is None:
                self._uow.sessions.create(
                    account_id,
                    slot=slot,
                    encrypted_session=None,
                    status=SessionStatus.EXPIRED,
                )


def recompute_account_status(uow, account_id: int):
    account = uow.accounts.get(account_id)
    if account.status in {"disabled", "deleted"}:
        return account
    sessions = uow.sessions.list_for_account(account_id)
    if _usable_session(sessions, DeveloperSlot.PRIMARY, SessionStatus.ACTIVE):
        return uow.accounts.mark_active(account_id)
    if any(_is_usable_standby(row) for row in sessions):
        return uow.accounts.mark_degraded(account_id)
    return uow.accounts.mark_reauth_required(account_id)


def _usable_session(sessions, slot: DeveloperSlot, status: SessionStatus) -> bool:
    return any(
        row.developer_slot == slot.value and row.status == status.value and bool(row.encrypted_session)
        for row in sessions
    )


def _is_usable_standby(row) -> bool:
    return (
        row.developer_slot in {DeveloperSlot.STANDBY_1.value, DeveloperSlot.STANDBY_2.value}
        and row.status == SessionStatus.STANDBY.value
        and bool(row.encrypted_session)
    )


def _required_telegram_identity(telegram_user_id: int) -> int:
    if telegram_user_id <= 0:
        raise AuthFailure("Telegram 未返回有效账号 identity，请重新开始绑定。", restart_required=True)
    return telegram_user_id


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
        purpose=AuthPurpose(model.purpose),
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


def _user_lock_key(user_id: int) -> int:
    return -user_id
