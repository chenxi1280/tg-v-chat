from __future__ import annotations

from tg_v_chat.bot.account_management.constants import (
    ACCOUNT_STATUS_ACTIVE,
    HELP_TEXT,
    PHONE_PATTERN,
    PHONE_PROMPT_TEXT,
    RELAY_HELP_TEXT,
    STATE_AWAITING_CODE,
    STATE_AWAITING_PASSWORD,
    STATE_AWAITING_PHONE,
)
from tg_v_chat.bot.account_management.parsing import _parse_id, _require_challenge
from tg_v_chat.bot.account_management.rendering import (
    _account_list_buttons,
    _accounts_text,
    _cancel_buttons,
    _choose_action_response,
    _code_prompt_response,
    _delete_confirm_buttons,
    _delete_confirm_text,
    _detail_buttons,
    _detail_text,
    _disable_confirm_buttons,
    _disable_confirm_text,
    _home_buttons,
    _home_nav_buttons,
    _home_text,
    _status_text,
)
from tg_v_chat.bot.account_management.state_helpers import (
    _auth_failure_response,
    _cancel_abandoned_bindings,
    _cancel_challenge_if_needed,
    _delete_account_for_user,
    _delete_incomplete_account,
)
from tg_v_chat.bot.code_keypad import parse_code_action, require_code_state
from tg_v_chat.bot.router import BotResponse
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeveloperSlot, MAX_BOUND_ACCOUNTS
from tg_v_chat.services.auth import AuthFailure, AuthService, AuthStep, TelegramAuthenticator
from tg_v_chat.storage.repositories import UnitOfWork


class AccountManagementService:
    def __init__(
        self,
        session_factory,
        authenticator: TelegramAuthenticator,
        cipher: SessionCipher,
    ):
        self._session_factory = session_factory
        self._authenticator = authenticator
        self._cipher = cipher

    def handle_command(self, telegram_user_id: int, command: str) -> BotResponse:
        if command in {"/start", "/admin"}:
            return self.home(telegram_user_id)
        if command == "/bind":
            return self.start_bind(telegram_user_id)
        if command == "/accounts":
            return self.accounts(telegram_user_id)
        return self.home(telegram_user_id)

    def handle_callback(self, telegram_user_id: int, data: str) -> BotResponse:
        actions = {
            "account.home": self.home,
            "account.bind.start": self.start_bind,
            "account.list": self.accounts,
            "account.status": self.status,
            "account.relay_help": self.relay_help,
            "account.help": self.help,
            "account.bind.cancel": self.cancel,
        }
        if data.startswith("account.detail:"):
            return self.detail(telegram_user_id, _parse_id(data))
        if data.startswith("account.code."):
            return self.handle_code_callback(telegram_user_id, data)
        if data.startswith("account.relogin:"):
            return self.relogin(telegram_user_id, _parse_id(data))
        if data.startswith("account.disable.confirm:"):
            return self.disable_confirm(telegram_user_id, _parse_id(data))
        if data.startswith("account.disable:"):
            return self.disable(telegram_user_id, _parse_id(data))
        if data.startswith("account.delete.confirm:"):
            return self.delete_confirm(telegram_user_id, _parse_id(data))
        if data.startswith("account.delete:"):
            return self.delete(telegram_user_id, _parse_id(data))
        handler = actions.get(data, self.home)
        return handler(telegram_user_id)

    def handle_text(self, telegram_user_id: int, text: str) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            state = uow.conversation_states.get(user.id)
            if state is None:
                return _choose_action_response()
            return self._continue_state(
                uow,
                telegram_user_id=telegram_user_id,
                user_id=user.id,
                state=state,
                text=text.strip(),
            )

    def home(self, telegram_user_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            uow.commit()
            accounts = uow.accounts.list_for_user(user.id)
            text = _home_text(accounts)
            return BotResponse(text, buttons=_home_buttons(accounts))

    def start_bind(self, telegram_user_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            _cancel_abandoned_bindings(uow, user.id)
            if uow.accounts.count_for_user(user.id) >= MAX_BOUND_ACCOUNTS:
                return BotResponse("每个系统用户最多绑定 20 个 Telegram 账号。", buttons=_home_nav_buttons())
            uow.conversation_states.set(user.id, STATE_AWAITING_PHONE)
            uow.commit()
        return BotResponse(PHONE_PROMPT_TEXT, buttons=_cancel_buttons())

    def accounts(self, telegram_user_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            accounts = uow.accounts.list_for_user(user.id)
            return BotResponse(_accounts_text(accounts), buttons=_account_list_buttons(accounts))

    def status(self, telegram_user_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            accounts = uow.accounts.list_for_user(user.id)
            return BotResponse(_status_text(accounts), buttons=_home_nav_buttons())

    def detail(self, telegram_user_id: int, account_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            account = uow.accounts.get_for_user(account_id, user.id)
            sessions = uow.sessions.list_for_account(account.id)
            return BotResponse(_detail_text(account, sessions), buttons=_detail_buttons(account))

    def disable_confirm(self, telegram_user_id: int, account_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            account = uow.accounts.get_for_user(account_id, user.id)
            return BotResponse(_disable_confirm_text(account), buttons=_disable_confirm_buttons(account.id))

    def disable(self, telegram_user_id: int, account_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            uow.accounts.get_for_user(account_id, user.id)
            uow.accounts.mark_disabled(account_id)
            uow.commit()
        return BotResponse("账号已禁用。", buttons=_home_nav_buttons())

    def delete_confirm(self, telegram_user_id: int, account_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            account = uow.accounts.get_for_user(account_id, user.id)
            return BotResponse(_delete_confirm_text(account), buttons=_delete_confirm_buttons(account.id))

    def delete(self, telegram_user_id: int, account_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            account = uow.accounts.get_for_user(account_id, user.id)
            _delete_account_for_user(uow, user.id, account)
            uow.commit()
        return BotResponse("账号已删除。", buttons=_home_nav_buttons())

    def relogin(self, telegram_user_id: int, account_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            account = uow.accounts.get_for_user(account_id, user.id)
            if account.status == ACCOUNT_STATUS_ACTIVE:
                return BotResponse("该账号当前无需重新登录。", buttons=_home_nav_buttons())
            phone_number = account.phone_number
            _delete_incomplete_account(uow, account.id)
            uow.conversation_states.clear(user.id)
            auth = AuthService(uow, self._authenticator, self._cipher)
            challenge = auth.start_binding(telegram_user_id, phone_number, DeveloperSlot.PRIMARY)
            uow.conversation_states.set(user.id, STATE_AWAITING_CODE, challenge.id)
            uow.commit()
            return _code_prompt_response(uow, challenge.id, "", detail="验证码已重新发送，请输入最新验证码。")

    def relay_help(self, _telegram_user_id: int) -> BotResponse:
        return BotResponse(RELAY_HELP_TEXT, buttons=_home_nav_buttons())

    def help(self, _telegram_user_id: int) -> BotResponse:
        return BotResponse(HELP_TEXT, buttons=_home_nav_buttons())

    def cancel(self, telegram_user_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            state = uow.conversation_states.get(user.id)
            _cancel_challenge_if_needed(uow, state)
            uow.conversation_states.clear(user.id)
            uow.commit()
        return BotResponse("已取消当前操作。", buttons=_home_nav_buttons())

    def _continue_state(self, uow, *, telegram_user_id: int, user_id: int, state, text: str) -> BotResponse:
        if state.state == STATE_AWAITING_PHONE:
            return self._bind_phone(uow, telegram_user_id=telegram_user_id, user_id=user_id, phone=text)
        if state.state == STATE_AWAITING_CODE:
            return _code_prompt_response(
                uow,
                state.auth_challenge_id,
                "",
                detail="请使用下方数字按钮输入 Telegram 验证码，不要直接发送验证码消息。",
            )
        if state.state == STATE_AWAITING_PASSWORD:
            return self._submit_password(uow, user_id=user_id, challenge_id=state.auth_challenge_id, password=text)
        raise RuntimeError(f"未知账号管理状态: {state.state}")

    def handle_code_callback(self, telegram_user_id: int, data: str) -> BotResponse:
        action = parse_code_action(data)
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            state = uow.conversation_states.get(user.id)
            require_code_state(state, action.challenge_id)
            if action.name == "digit":
                return _code_prompt_response(uow, action.challenge_id, f"{action.buffer}{action.value}")
            if action.name == "backspace":
                return _code_prompt_response(uow, action.challenge_id, action.buffer[:-1])
            if action.name == "clear":
                return _code_prompt_response(uow, action.challenge_id, "")
            if action.name == "submit":
                return self._submit_keypad_code(
                    uow,
                    user_id=user.id,
                    challenge_id=action.challenge_id,
                    code=action.buffer,
                )
            if action.name == "resend":
                return self._resend_code(
                    uow,
                    telegram_user_id=telegram_user_id,
                    user_id=user.id,
                    challenge_id=action.challenge_id,
                )
        raise RuntimeError(f"未知验证码按钮操作: {action.name}")

    def _bind_phone(self, uow, *, telegram_user_id: int, user_id: int, phone: str) -> BotResponse:
        if not PHONE_PATTERN.match(phone):
            return BotResponse("手机号需包含国家区号，例如 +8613812345678。", buttons=_cancel_buttons())
        auth = AuthService(uow, self._authenticator, self._cipher)
        challenge = auth.start_binding(telegram_user_id, phone, DeveloperSlot.PRIMARY)
        uow.conversation_states.set(user_id, STATE_AWAITING_CODE, challenge.id)
        uow.commit()
        return _code_prompt_response(uow, challenge.id, "")

    def _submit_keypad_code(self, uow, *, user_id: int, challenge_id: int, code: str) -> BotResponse:
        if not code:
            return _code_prompt_response(uow, challenge_id, "", detail="请先使用数字按钮输入验证码。")
        auth = AuthService(uow, self._authenticator, self._cipher)
        try:
            step = auth.submit_code(challenge_id, code)
        except AuthFailure as exc:
            if exc.restart_required:
                return _auth_failure_response(uow, user_id=user_id, challenge_id=challenge_id, failure=exc)
            return _code_prompt_response(uow, challenge_id, code, detail=exc.message)
        if step is AuthStep.PASSWORD_REQUIRED:
            uow.conversation_states.set(user_id, STATE_AWAITING_PASSWORD, challenge_id)
            uow.commit()
            return BotResponse("该账号开启了 2FA，请输入二次密码。", buttons=_cancel_buttons())
        uow.conversation_states.clear(user_id)
        uow.commit()
        return BotResponse("绑定成功。", buttons=_home_nav_buttons())

    def _resend_code(self, uow, *, telegram_user_id: int, user_id: int, challenge_id: int) -> BotResponse:
        challenge = uow.auth_challenges.get(challenge_id)
        phone_number = challenge.phone_number
        _delete_incomplete_account(uow, challenge.bound_tg_account_id)
        uow.conversation_states.clear(user_id)
        auth = AuthService(uow, self._authenticator, self._cipher)
        new_challenge = auth.start_binding(telegram_user_id, phone_number, DeveloperSlot.PRIMARY)
        uow.conversation_states.set(user_id, STATE_AWAITING_CODE, new_challenge.id)
        uow.commit()
        return _code_prompt_response(uow, new_challenge.id, "", detail="验证码已重新发送，请输入最新验证码。")

    def _submit_password(self, uow, *, user_id: int, challenge_id: int | None, password: str) -> BotResponse:
        auth = AuthService(uow, self._authenticator, self._cipher)
        try:
            auth.submit_password(_require_challenge(challenge_id), password)
        except AuthFailure as exc:
            return _auth_failure_response(uow, user_id=user_id, challenge_id=challenge_id, failure=exc)
        uow.conversation_states.clear(user_id)
        uow.commit()
        return BotResponse("绑定成功。", buttons=_home_nav_buttons())
