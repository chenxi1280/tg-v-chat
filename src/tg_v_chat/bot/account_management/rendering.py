from __future__ import annotations

from tg_v_chat.bot.account_management.constants import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_BINDING,
    ACCOUNT_STATUS_DISABLED,
)
from tg_v_chat.bot.account_management.parsing import _require_challenge
from tg_v_chat.bot.code_keypad import CodePrompt, code_prompt_response as build_code_prompt_response
from tg_v_chat.bot.router import BotResponse, ButtonSpec
from tg_v_chat.domain import MAX_BOUND_ACCOUNTS, DeveloperSlot


def _mask_phone(phone: str) -> str:
    if len(phone) <= 8:
        return f"{phone[:2]}****"
    return f"{phone[:6]}****{phone[-4:]}"


def _account_name(account) -> str:
    return account.display_name or "未命名账号"


def _account_username(account) -> str:
    username = account.username
    if not username:
        return "未设置"
    return username if username.startswith("@") else f"@{username}"


def _account_label(account) -> str:
    return f"{_account_name(account)}（{_account_username(account)}）"


def _first_relogin_account(accounts):
    return next((account for account in accounts if account.status == ACCOUNT_STATUS_BINDING), None)


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
    lines.extend(f"{index}. {_account_label(item)}｜{item.status}" for index, item in enumerate(accounts, 1))
    return "\n".join(lines)


def _account_list_buttons(accounts) -> tuple[ButtonSpec, ...]:
    buttons = [ButtonSpec(_account_label(account), f"account.detail:{account.id}") for account in accounts]
    buttons.append(ButtonSpec("绑定 TG 账号", "account.bind.start"))
    buttons.append(ButtonSpec("返回首页", "account.home"))
    return tuple(buttons)


def _detail_text(account, sessions) -> str:
    session_status = {item.developer_slot: item.status for item in sessions}
    return "\n".join(
        (
            "账号详情",
            "",
            f"接收账号：{_account_name(account)}",
            f"用户名：{_account_username(account)}",
            f"手机号：{_mask_phone(account.phone_number)}",
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
    lines.extend(f"{_account_label(item)}：{item.status}" for item in accounts)
    return "\n".join(lines)


def _code_prompt_response(uow, challenge_id: int | None, buffer: str, *, detail: str | None = None) -> BotResponse:
    challenge = uow.auth_challenges.get(_require_challenge(challenge_id))
    return build_code_prompt_response(CodePrompt(challenge.id, _mask_phone(challenge.phone_number), buffer, detail))


def _choose_action_response() -> BotResponse:
    return BotResponse("请通过下面按钮选择要进行的账号管理操作。", buttons=_home_nav_buttons())


def _home_nav_buttons() -> tuple[ButtonSpec, ...]:
    return (ButtonSpec("返回账号管理", "account.home"),)


def _cancel_buttons() -> tuple[ButtonSpec, ...]:
    return (ButtonSpec("取消绑定", "account.bind.cancel"), ButtonSpec("返回首页", "account.home"))


def _detail_buttons(account, sessions) -> tuple[ButtonSpec, ...]:
    buttons = [
        ButtonSpec("删除账号", f"account.delete.confirm:{account.id}"),
        ButtonSpec("返回账号列表", "account.list"),
        ButtonSpec("返回首页", "account.home"),
    ]
    if account.status != "disabled":
        buttons[0:0] = _slot_buttons(account.id, sessions)
        buttons.insert(0, ButtonSpec("禁用账号", f"account.disable.confirm:{account.id}"))
    return tuple(buttons)


def _slot_buttons(account_id: int, sessions) -> list[ButtonSpec]:
    by_slot = {row.developer_slot: row for row in sessions}
    labels = {
        DeveloperSlot.PRIMARY: "主授权",
        DeveloperSlot.STANDBY_1: "备用 1",
        DeveloperSlot.STANDBY_2: "备用 2",
    }
    buttons = []
    for slot in DeveloperSlot:
        authorized = bool(getattr(by_slot.get(slot.value), "encrypted_session", None))
        action = "reauth" if authorized else "bind"
        verb = "重新授权" if authorized else "授权"
        buttons.append(ButtonSpec(f"{labels[slot]}{verb}", f"account.slot.{action}:{account_id}:{slot.value}"))
    return buttons


def _disable_confirm_text(account) -> str:
    return "\n".join(
        (
            "确认禁用这个账号？",
            "",
            f"账号：{_account_label(account)}",
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
            f"账号：{_account_label(account)}",
            "删除后会从账号管理列表移除；未完成登录的账号会直接清除。",
        )
    )


def _delete_confirm_buttons(account_id: int) -> tuple[ButtonSpec, ...]:
    return (
        ButtonSpec("确认删除", f"account.delete:{account_id}"),
        ButtonSpec("取消", f"account.detail:{account_id}"),
    )


def _relogin_nav_buttons(account_id: int | None) -> tuple[ButtonSpec, ...]:
    if account_id is None:
        return _home_nav_buttons()
    return (
        ButtonSpec("重新登录", f"account.relogin:{account_id}"),
        ButtonSpec("返回账号管理", "account.home"),
    )


def _slot_auth_nav_buttons(account_id: int, slot: str, action: str) -> tuple[ButtonSpec, ...]:
    label = "重新授权该槽位" if action == "reauth" else "授权该槽位"
    return (
        ButtonSpec(label, f"account.slot.{action}:{account_id}:{slot}"),
        ButtonSpec("返回账号管理", "account.home"),
    )
