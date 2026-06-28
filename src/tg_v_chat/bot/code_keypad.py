from __future__ import annotations

from dataclasses import dataclass

from tg_v_chat.bot.router import BotResponse, ButtonSpec


@dataclass(frozen=True)
class CodeButtonAction:
    name: str
    challenge_id: int
    buffer: str = ""
    value: str = ""


@dataclass(frozen=True)
class CodePrompt:
    challenge_id: int
    masked_phone: str
    buffer: str = ""
    detail: str | None = None


def code_prompt_response(prompt: CodePrompt) -> BotResponse:
    return BotResponse(_code_prompt_text(prompt), buttons=_code_buttons(prompt.challenge_id, prompt.buffer))


def parse_code_action(data: str) -> CodeButtonAction:
    body = data.removeprefix("account.code.")
    name, raw = body.split(":", 1)
    if name == "digit":
        challenge_id, buffer, digit = raw.split(":", 2)
        if digit not in "0123456789":
            raise ValueError(f"无效验证码数字: {digit}")
        return CodeButtonAction(name, int(challenge_id), buffer, digit)
    if name in {"backspace", "submit"}:
        parts = raw.split(":", 1)
        buffer = parts[1] if len(parts) > 1 else ""
        return CodeButtonAction(name, int(parts[0]), buffer)
    if name in {"clear", "resend"}:
        return CodeButtonAction(name, int(raw))
    raise ValueError(f"无效验证码按钮操作: {data}")


def require_code_state(state, challenge_id: int) -> None:
    if state is None or state.state != "awaiting_code":
        raise RuntimeError("当前没有等待验证码的绑定会话。")
    if state.auth_challenge_id != challenge_id:
        raise RuntimeError("验证码按钮已失效，请重新打开当前绑定会话。")


def _code_prompt_text(prompt: CodePrompt) -> str:
    count = len(prompt.buffer)
    current = f"{_mask_code_buffer(prompt.buffer)}（已输入 {count} 位）" if count else "未输入"
    lines = [
        "验证码已发送",
        "",
        f"手机号：{prompt.masked_phone}",
        "请使用下方数字按钮输入 Telegram 验证码，不要直接发送验证码消息。",
        "为避免验证码被 Telegram 判定失效，Bot 不会在聊天中接收明文验证码。",
        "",
        f"当前输入：{current}",
        "",
        "若提示验证码已过期，请点击「重发验证码」后输入最新验证码。",
    ]
    if prompt.detail:
        lines.insert(6, f"提示：{prompt.detail}")
        lines.insert(6, "")
    return "\n".join(lines)


def _code_buttons(challenge_id: int, buffer: str) -> tuple[ButtonSpec, ...]:
    return (
        ButtonSpec("1", _code_digit_data(challenge_id, buffer, "1")),
        ButtonSpec("2", _code_digit_data(challenge_id, buffer, "2")),
        ButtonSpec("3", _code_digit_data(challenge_id, buffer, "3")),
        ButtonSpec("4", _code_digit_data(challenge_id, buffer, "4")),
        ButtonSpec("5", _code_digit_data(challenge_id, buffer, "5")),
        ButtonSpec("6", _code_digit_data(challenge_id, buffer, "6")),
        ButtonSpec("7", _code_digit_data(challenge_id, buffer, "7")),
        ButtonSpec("8", _code_digit_data(challenge_id, buffer, "8")),
        ButtonSpec("9", _code_digit_data(challenge_id, buffer, "9")),
        ButtonSpec("清空", f"account.code.clear:{challenge_id}"),
        ButtonSpec("0", _code_digit_data(challenge_id, buffer, "0")),
        ButtonSpec("删除", f"account.code.backspace:{challenge_id}:{buffer}"),
        ButtonSpec("✅ 提交", f"account.code.submit:{challenge_id}:{buffer}"),
        ButtonSpec("重发验证码", f"account.code.resend:{challenge_id}"),
        ButtonSpec("取消绑定", "account.bind.cancel"),
    )


def _code_digit_data(challenge_id: int, buffer: str, digit: str) -> str:
    return f"account.code.digit:{challenge_id}:{buffer}:{digit}"


def _mask_code_buffer(buffer: str) -> str:
    return "•" * len(buffer)
