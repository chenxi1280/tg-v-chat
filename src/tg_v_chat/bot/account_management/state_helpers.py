from __future__ import annotations

from tg_v_chat.bot.account_management.constants import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_BINDING,
    AUTH_STATUS_CANCELLED,
    AUTH_STATUS_EXPIRED,
)
from tg_v_chat.bot.account_management.rendering import _cancel_buttons, _relogin_nav_buttons
from tg_v_chat.bot.router import BotResponse
from tg_v_chat.services.auth import AuthFailure, AuthStep


def _cancel_abandoned_bindings(uow, user_id: int) -> None:
    active_challenge_id = _active_challenge_id(uow, user_id)
    for account in uow.accounts.list_by_status_for_user(user_id, ACCOUNT_STATUS_BINDING):
        challenges = uow.auth_challenges.list_for_account(account.id)
        if any(challenge.id == active_challenge_id for challenge in challenges):
            continue
        _cancel_challenges(uow, challenges)
        uow.accounts.mark_disabled(account.id)


def _active_challenge_id(uow, user_id: int) -> int | None:
    state = uow.conversation_states.get(user_id)
    return None if state is None else state.auth_challenge_id


def _cancel_challenges(uow, challenges) -> None:
    for challenge in challenges:
        if challenge.status != AuthStep.COMPLETE.value:
            uow.auth_challenges.mark_status(challenge.id, AUTH_STATUS_CANCELLED)


def _delete_incomplete_account(uow, account_id: int) -> None:
    uow.auth_challenges.delete_for_account(account_id)
    uow.accounts.delete(account_id)


def _delete_account_for_user(uow, user_id: int, account) -> None:
    state = uow.conversation_states.get(user_id)
    if state is not None and _state_belongs_to_account(uow, state, account.id):
        uow.conversation_states.clear(user_id)
    uow.auth_challenges.delete_for_account(account.id)
    uow.sessions.delete_for_account(account.id)
    if account.status != ACCOUNT_STATUS_ACTIVE:
        uow.accounts.delete(account.id)
        return
    uow.accounts.mark_deleted(account.id)


def _state_belongs_to_account(uow, state, account_id: int) -> bool:
    if state.auth_challenge_id is None:
        return False
    challenge = uow.auth_challenges.get(state.auth_challenge_id)
    return challenge.bound_tg_account_id == account_id


def _cancel_challenge_if_needed(uow, state) -> None:
    if state is None or state.auth_challenge_id is None:
        return
    _cancel_challenge_by_id(uow, state.auth_challenge_id)


def _cancel_challenge_by_id(
    uow,
    challenge_id: int | None,
    status: str = AUTH_STATUS_CANCELLED,
    *,
    disable_account: bool = True,
) -> int | None:
    if challenge_id is None:
        return None
    challenge = uow.auth_challenges.mark_status(challenge_id, status)
    if disable_account:
        uow.accounts.mark_disabled(challenge.bound_tg_account_id)
    return challenge.bound_tg_account_id


def _auth_failure_response(uow, *, user_id: int, challenge_id: int | None, failure: AuthFailure) -> BotResponse:
    if not failure.restart_required:
        return BotResponse(failure.message, buttons=_cancel_buttons())
    account_id = _cancel_challenge_by_id(uow, challenge_id, AUTH_STATUS_EXPIRED, disable_account=False)
    uow.conversation_states.clear(user_id)
    uow.commit()
    return BotResponse(failure.message, buttons=_relogin_nav_buttons(account_id))
