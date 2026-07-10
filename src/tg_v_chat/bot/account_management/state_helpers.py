from __future__ import annotations

from tg_v_chat.bot.account_management.constants import (
    ACCOUNT_STATUS_BINDING,
    AUTH_STATUS_CANCELLED,
    AUTH_STATUS_EXPIRED,
    STATE_AWAITING_PASSWORD,
)
from tg_v_chat.bot.account_management.rendering import (
    _cancel_buttons,
    _relogin_nav_buttons,
    _slot_auth_nav_buttons,
)
from tg_v_chat.bot.router import BotResponse
from tg_v_chat.domain import DeveloperSlot
from tg_v_chat.services.auth import AuthFailure, AuthPurpose, AuthStep


def _resume_pending_password_binding(uow, user_id: int) -> BotResponse | None:
    accounts = uow.accounts.list_for_user(user_id)
    for account in (item for item in accounts if item.status != "disabled"):
        challenge = _password_required_challenge(uow, account.id)
        if challenge is None:
            continue
        uow.conversation_states.set(user_id, STATE_AWAITING_PASSWORD, challenge.id)
        uow.commit()
        return BotResponse("检测到未完成的 2FA 验证，请输入二次密码。", buttons=_cancel_buttons())
    return None


def _password_required_challenge(uow, account_id: int):
    for challenge in uow.auth_challenges.list_for_account(account_id):
        if challenge.status == AuthStep.PASSWORD_REQUIRED.value:
            return challenge
    return None


def _cancel_abandoned_bindings(uow, user_id: int) -> None:
    active_challenge_id = _active_challenge_id(uow, user_id)
    for account in uow.accounts.list_by_status_for_user(user_id, ACCOUNT_STATUS_BINDING):
        with uow.account_locks.acquire(account.id):
            challenges = uow.auth_challenges.list_for_account(account.id)
            if any(challenge.id == active_challenge_id for challenge in challenges):
                continue
            _cancel_challenges(uow, challenges)
            uow.accounts.mark_disabled(account.id)
            uow.commit()


def _active_challenge_id(uow, user_id: int) -> int | None:
    state = uow.conversation_states.get(user_id)
    return None if state is None else state.auth_challenge_id


def _cancel_challenges(uow, challenges) -> None:
    for challenge in challenges:
        if challenge.status != AuthStep.COMPLETE.value:
            uow.auth_challenges.mark_status(challenge.id, AUTH_STATUS_CANCELLED)


def _delete_account_for_user(uow, user_id: int, account) -> None:
    original_status = account.status
    state = uow.conversation_states.get(user_id)
    if state is not None and _state_belongs_to_account(uow, state, account.id):
        uow.conversation_states.clear(user_id)
    uow.auth_challenges.delete_for_account(account.id)
    uow.mappings.invalidate_for_account(account.id)
    uow.sessions.delete_for_account(account.id)
    if original_status == ACCOUNT_STATUS_BINDING:
        uow.accounts.delete(account.id)
        return
    uow.accounts.mark_deleted(account.id)


def _state_belongs_to_account(uow, state, account_id: int) -> bool:
    if state.auth_challenge_id is None:
        return False
    challenge = uow.auth_challenges.get(state.auth_challenge_id)
    return challenge.bound_tg_account_id == account_id


def _cancel_challenge_if_needed(uow, state) -> str:
    if state is None or state.auth_challenge_id is None:
        return "none"
    _account_id, final_status = _cancel_challenge_by_id(uow, state.auth_challenge_id)
    return final_status


def _cancel_challenge_by_id(
    uow,
    challenge_id: int | None,
    status: str = AUTH_STATUS_CANCELLED,
    *,
    disable_account: bool = True,
) -> tuple[int | None, str]:
    if challenge_id is None:
        return None, "none"
    challenge = uow.auth_challenges.get(challenge_id)
    with uow.account_locks.acquire(challenge.bound_tg_account_id):
        uow.session.refresh(challenge)
        final_status = challenge.status
        if challenge.status != AuthStep.COMPLETE.value:
            challenge = uow.auth_challenges.mark_status(challenge_id, status)
            final_status = challenge.status
        is_active_initial_bind = (
            challenge.purpose == AuthPurpose.INITIAL_BIND.value
            and final_status != AuthStep.COMPLETE.value
        )
        if disable_account and is_active_initial_bind:
            uow.accounts.mark_disabled(challenge.bound_tg_account_id)
        uow.commit()
        return challenge.bound_tg_account_id, final_status


def _auth_failure_response(uow, *, user_id: int, challenge_id: int | None, failure: AuthFailure) -> BotResponse:
    if not failure.restart_required:
        return BotResponse(failure.message, buttons=_cancel_buttons())
    challenge = uow.auth_challenges.get(challenge_id) if challenge_id is not None else None
    account_id, _status = _cancel_challenge_by_id(uow, challenge_id, AUTH_STATUS_EXPIRED, disable_account=False)
    uow.conversation_states.clear(user_id)
    uow.commit()
    if challenge is not None and challenge.purpose == AuthPurpose.SLOT_AUTHORIZATION.value:
        session = uow.sessions.get_for_account(
            challenge.bound_tg_account_id,
            DeveloperSlot(challenge.developer_slot),
        )
        action = "reauth" if session is not None and session.encrypted_session else "bind"
        return BotResponse(
            failure.message,
            buttons=_slot_auth_nav_buttons(account_id, challenge.developer_slot, action),
        )
    return BotResponse(failure.message, buttons=_relogin_nav_buttons(account_id))
