from __future__ import annotations

import re

from tg_v_chat.bot.router import BotResponse, ButtonSpec
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeveloperSlot, MAX_BOUND_ACCOUNTS
from tg_v_chat.services.auth import AuthFailure, AuthService, AuthStep, TelegramAuthenticator
from tg_v_chat.storage.repositories import UnitOfWork


STATE_AWAITING_PHONE = "awaiting_phone"
STATE_AWAITING_CODE = "awaiting_code"
STATE_AWAITING_PASSWORD = "awaiting_password"
ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_BINDING = "binding"
ACCOUNT_STATUS_DISABLED = "disabled"
ACCOUNT_STATUS_DELETED = "deleted"
AUTH_STATUS_CANCELLED = "cancelled"
PHONE_PATTERN = re.compile(r"^\+\d{8,15}$")


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
            return self._continue_state(uow, telegram_user_id, user.id, state, text.strip())

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
        return BotResponse(_phone_prompt(), buttons=_cancel_buttons())

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
        return BotResponse("验证码已重新发送，请输入 Telegram 收到的验证码。", buttons=_cancel_buttons())

    def relay_help(self, _telegram_user_id: int) -> BotResponse:
        return BotResponse(_relay_help_text(), buttons=_home_nav_buttons())

    def help(self, _telegram_user_id: int) -> BotResponse:
        return BotResponse(_help_text(), buttons=_home_nav_buttons())

    def cancel(self, telegram_user_id: int) -> BotResponse:
        with UnitOfWork(self._session_factory) as uow:
            user = uow.users.get_or_create(telegram_user_id)
            state = uow.conversation_states.get(user.id)
            _cancel_challenge_if_needed(uow, state)
            uow.conversation_states.clear(user.id)
            uow.commit()
        return BotResponse("已取消当前操作。", buttons=_home_nav_buttons())

    def _continue_state(self, uow, telegram_user_id: int, user_id: int, state, text: str) -> BotResponse:
        if state.state == STATE_AWAITING_PHONE:
            return self._bind_phone(uow, telegram_user_id, user_id, text)
        if state.state == STATE_AWAITING_CODE:
            return self._submit_code(uow, user_id, state.auth_challenge_id, text)
        if state.state == STATE_AWAITING_PASSWORD:
            return self._submit_password(uow, user_id, state.auth_challenge_id, text)
        raise RuntimeError(f"未知账号管理状态: {state.state}")

    def _bind_phone(self, uow, telegram_user_id: int, user_id: int, phone: str) -> BotResponse:
        if not PHONE_PATTERN.match(phone):
            return BotResponse("手机号需包含国家区号，例如 +8613812345678。", buttons=_cancel_buttons())
        auth = AuthService(uow, self._authenticator, self._cipher)
        challenge = auth.start_binding(telegram_user_id, phone, DeveloperSlot.PRIMARY)
        uow.conversation_states.set(user_id, STATE_AWAITING_CODE, challenge.id)
        uow.commit()
        return BotResponse("验证码已发送，请输入 Telegram 收到的验证码。", buttons=_cancel_buttons())

    def _submit_code(self, uow, user_id: int, challenge_id: int | None, code: str) -> BotResponse:
        auth = AuthService(uow, self._authenticator, self._cipher)
        try:
            step = auth.submit_code(_require_challenge(challenge_id), code)
        except AuthFailure as exc:
            return _auth_failure_response(uow, user_id, challenge_id, exc)
        if step is AuthStep.PASSWORD_REQUIRED:
            uow.conversation_states.set(user_id, STATE_AWAITING_PASSWORD, challenge_id)
            uow.commit()
            return BotResponse("该账号开启了 2FA，请输入二次密码。", buttons=_cancel_buttons())
        uow.conversation_states.clear(user_id)
        uow.commit()
        return BotResponse("绑定成功。", buttons=_home_nav_buttons())

    def _submit_password(self, uow, user_id: int, challenge_id: int | None, password: str) -> BotResponse:
        auth = AuthService(uow, self._authenticator, self._cipher)
        try:
            auth.submit_password(_require_challenge(challenge_id), password)
        except AuthFailure as exc:
            return _auth_failure_response(uow, user_id, challenge_id, exc)
        uow.conversation_states.clear(user_id)
        uow.commit()
        return BotResponse("绑定成功。", buttons=_home_nav_buttons())


def _home_text(accounts) -> str:
    if not accounts:
        return "\n".join(
            (
                "账号管理",
                "",
                "你还没有绑定要管理的 TG 账号。",
                "绑定后，该账号收到的私聊会推送到这里，你回复 Bot 推送消息即可代发。",
            )
        )
    active_count = sum(1 for account in accounts if account.status == ACCOUNT_STATUS_ACTIVE)
    attention_count = sum(1 for account in accounts if account.status not in {ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_DISABLED})
    return f"账号管理\n\n已绑定账号：{active_count}/{MAX_BOUND_ACCOUNTS}\n需要处理：{attention_count}\n\n请选择要进行的操作。"


def _home_buttons(accounts) -> tuple[ButtonSpec, ...]:
    if not accounts:
        return (
            ButtonSpec("绑定 TG 账号", "account.bind.start"),
            ButtonSpec("中转说明", "account.relay_help"),
            ButtonSpec("帮助", "account.help"),
        )
    buttons = [
        ButtonSpec("绑定 TG 账号", "account.bind.start"),
        ButtonSpec("我的账号", "account.list"),
        ButtonSpec("授权状态", "account.status"),
        ButtonSpec("中转说明", "account.relay_help"),
        ButtonSpec("帮助", "account.help"),
    ]
    relogin_account = _first_relogin_account(accounts)
    if relogin_account is not None:
        buttons.insert(0, ButtonSpec("重新登录", f"account.relogin:{relogin_account.id}"))
    return tuple(buttons)


def _accounts_text(accounts) -> str:
    if not accounts:
        return "我的账号\n\n暂无绑定账号。"
    lines = ["我的账号", ""]
    lines.extend(f"{index}. {_mask_phone(item.phone_number)}｜{item.status}" for index, item in enumerate(accounts, 1))
    return "\n".join(lines)


def _account_list_buttons(accounts) -> tuple[ButtonSpec, ...]:
    buttons = [ButtonSpec(_mask_phone(account.phone_number), f"account.detail:{account.id}") for account in accounts]
    buttons.append(ButtonSpec("绑定 TG 账号", "account.bind.start"))
    buttons.append(ButtonSpec("返回首页", "account.home"))
    return tuple(buttons)


def _detail_text(account, sessions) -> str:
    session_status = {item.developer_slot: item.status for item in sessions}
    return "\n".join(
        (
            "账号详情",
            "",
            f"账号：{_mask_phone(account.phone_number)}",
            f"状态：{account.status}",
            f"主授权：{session_status.get('primary', 'missing')}",
            f"备用 1：{session_status.get('standby_1', 'missing')}",
            f"备用 2：{session_status.get('standby_2', 'missing')}",
        )
    )


def _status_text(accounts) -> str:
    if not accounts:
        return "授权状态\n\n暂无绑定账号。"
    lines = ["授权状态", ""]
    lines.extend(f"{_mask_phone(item.phone_number)}：{item.status}" for item in accounts)
    return "\n".join(lines)


def _relay_help_text() -> str:
    return "中转说明\n\n绑定账号收到私聊后，Bot 会把消息推送到这里。请直接回复 Bot 推送的原消息进行代发。"


def _help_text() -> str:
    return "帮助\n\n使用按钮完成绑定和查看账号。绑定与中转失败会直接返回明确错误。"


def _phone_prompt() -> str:
    return "请输入要管理的 TG 账号手机号，需包含国家区号，例如 +8613812345678。"


def _choose_action_response() -> BotResponse:
    return BotResponse("请通过下面按钮选择要进行的账号管理操作。", buttons=_home_nav_buttons())


def _home_nav_buttons() -> tuple[ButtonSpec, ...]:
    return (ButtonSpec("返回账号管理", "account.home"),)


def _cancel_buttons() -> tuple[ButtonSpec, ...]:
    return (ButtonSpec("取消绑定", "account.bind.cancel"), ButtonSpec("返回首页", "account.home"))


def _detail_buttons(account) -> tuple[ButtonSpec, ...]:
    buttons = [
        ButtonSpec("删除账号", f"account.delete.confirm:{account.id}"),
        ButtonSpec("返回账号列表", "account.list"),
        ButtonSpec("返回首页", "account.home"),
    ]
    if account.status != "disabled":
        buttons.insert(0, ButtonSpec("禁用账号", f"account.disable.confirm:{account.id}"))
    return tuple(buttons)


def _disable_confirm_text(account) -> str:
    return "\n".join(
        (
            "确认禁用这个账号？",
            "",
            f"账号：{_mask_phone(account.phone_number)}",
            "禁用后不会继续监听该账号私聊，也不会使用它代发。",
            "历史中转记录保留。",
        )
    )


def _disable_confirm_buttons(account_id: int) -> tuple[ButtonSpec, ...]:
    return (
        ButtonSpec("确认禁用", f"account.disable:{account_id}"),
        ButtonSpec("取消", f"account.detail:{account_id}"),
    )


def _delete_confirm_text(account) -> str:
    return "\n".join(
        (
            "确认删除这个账号？",
            "",
            f"账号：{_mask_phone(account.phone_number)}",
            "删除后会从账号管理列表移除；未完成登录的账号会直接清除。",
        )
    )


def _delete_confirm_buttons(account_id: int) -> tuple[ButtonSpec, ...]:
    return (
        ButtonSpec("确认删除", f"account.delete:{account_id}"),
        ButtonSpec("取消", f"account.detail:{account_id}"),
    )


def _mask_phone(phone: str) -> str:
    if len(phone) <= 8:
        return f"{phone[:2]}****"
    return f"{phone[:6]}****{phone[-4:]}"


def _require_challenge(challenge_id: int | None) -> int:
    if challenge_id is None:
        raise RuntimeError("当前绑定状态缺少认证挑战，请重新开始绑定。")
    return challenge_id


def _auth_failure_response(uow, user_id: int, challenge_id: int | None, failure: AuthFailure) -> BotResponse:
    if not failure.restart_required:
        return BotResponse(failure.message, buttons=_cancel_buttons())
    account_id = _cancel_challenge_by_id(uow, challenge_id)
    uow.conversation_states.clear(user_id)
    uow.commit()
    return BotResponse(failure.message, buttons=_relogin_nav_buttons(account_id))


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
    if account.status != ACCOUNT_STATUS_ACTIVE:
        uow.accounts.delete(account.id)
        return
    uow.sessions.delete_for_account(account.id)
    uow.accounts.mark_deleted(account.id)


def _state_belongs_to_account(uow, state, account_id: int) -> bool:
    if state.auth_challenge_id is None:
        return False
    challenge = uow.auth_challenges.get(state.auth_challenge_id)
    return challenge.bound_tg_account_id == account_id


def _first_relogin_account(accounts):
    return next((account for account in accounts if account.status == ACCOUNT_STATUS_BINDING), None)


def _relogin_nav_buttons(account_id: int | None) -> tuple[ButtonSpec, ...]:
    if account_id is None:
        return _home_nav_buttons()
    return (
        ButtonSpec("重新登录", f"account.relogin:{account_id}"),
        ButtonSpec("返回账号管理", "account.home"),
    )


def _parse_id(data: str) -> int:
    try:
        return int(data.rsplit(":", 1)[1])
    except ValueError as exc:
        raise ValueError(f"无效账号操作: {data}") from exc


def _cancel_challenge_if_needed(uow, state) -> None:
    if state is None or state.auth_challenge_id is None:
        return
    _cancel_challenge_by_id(uow, state.auth_challenge_id)


def _cancel_challenge_by_id(uow, challenge_id: int | None) -> int | None:
    if challenge_id is None:
        return None
    challenge = uow.auth_challenges.mark_status(challenge_id, AUTH_STATUS_CANCELLED)
    uow.accounts.mark_disabled(challenge.bound_tg_account_id)
    return challenge.bound_tg_account_id
