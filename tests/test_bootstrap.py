import pytest

from tg_v_chat.bot.handlers import BotReplyHandler
from tg_v_chat.bot.router import BotIncomingMessage, BotUpdateRouter
from tg_v_chat.config import load_config
from tg_v_chat.main import build_runtime
from tg_v_chat.runtime import run_role
from tg_v_chat.telegram.telethon_clients import TelethonBotGateway, TelethonSenderPool, _buttons
from tg_v_chat.workers.runner import WorkerRunner


class FakeAccountManagement:
    def handle_command(self, telegram_user_id, command):
        from tg_v_chat.bot.router import BotResponse, ButtonSpec

        return BotResponse(
            f"账号管理 {telegram_user_id} {command}",
            buttons=(ButtonSpec("绑定 TG 账号", "account.bind.start"),),
        )

    def handle_callback(self, telegram_user_id, data):
        from tg_v_chat.bot.router import BotResponse

        return BotResponse(f"callback {telegram_user_id} {data}")

    def handle_text(self, telegram_user_id, text):
        from tg_v_chat.bot.router import BotResponse

        return BotResponse(f"text {telegram_user_id} {text}")


def test_load_config_requires_secrets(monkeypatch):
    for name in ("TG_V_CHAT_DATABASE_URL", "TG_V_CHAT_SESSION_KEY", "TG_V_CHAT_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="TG_V_CHAT_DATABASE_URL"):
        load_config()


def test_load_config_rejects_template_placeholders(monkeypatch):
    required_values = {
        "TG_V_CHAT_DATABASE_URL": "postgresql+psycopg://app_user:secret@postgres:5432/tg_v_chat",
        "TG_V_CHAT_SESSION_KEY": "replace_with_32_plus_char_random_secret",
        "TG_V_CHAT_BOT_TOKEN": "bot-token",
        "TG_V_CHAT_PRIMARY_API_ID": "1",
        "TG_V_CHAT_PRIMARY_API_HASH": "hash",
        "TG_V_CHAT_STANDBY_1_API_ID": "2",
        "TG_V_CHAT_STANDBY_1_API_HASH": "hash",
        "TG_V_CHAT_STANDBY_2_API_ID": "3",
        "TG_V_CHAT_STANDBY_2_API_HASH": "hash",
    }
    for name, value in required_values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="TG_V_CHAT_SESSION_KEY"):
        load_config()


def test_runtime_entrypoints_are_constructible():
    runtime = build_runtime("sqlite:///:memory:", "test-key", "bot-token", allow_sqlite_for_tests=True)

    assert isinstance(runtime.bot_handler, BotReplyHandler)
    assert isinstance(runtime.bot_gateway, TelethonBotGateway)
    assert isinstance(runtime.sender_pool, TelethonSenderPool)
    assert isinstance(runtime.worker_runner, WorkerRunner)


def test_runtime_rejects_sqlite_without_test_opt_in():
    with pytest.raises(ValueError, match="PostgreSQL"):
        build_runtime("sqlite:///:memory:", "test-key", "bot-token")


def test_bot_router_replies_to_start_and_admin_commands():
    router = BotUpdateRouter(
        lambda _command: None,
        FakeAccountManagement(),
    )

    start = router.handle(BotIncomingMessage(146517, 10, None, "/start"))
    admin = router.handle(BotIncomingMessage(146517, 11, None, "/admin"))

    assert start[0].text == "账号管理 146517 /start"
    assert start[0].reply_to_message_id == 10
    assert [button.text for button in start[0].buttons] == ["绑定 TG 账号"]
    assert admin[0].text == "账号管理 146517 /admin"


def test_telegram_login_code_buttons_are_rendered_as_keypad_rows():
    from tg_v_chat.bot.code_keypad import CodePrompt, code_prompt_response

    response = code_prompt_response(CodePrompt(7, "+15550****0001"))
    rows = _buttons(response)

    assert [[button.text for button in row] for row in rows] == [
        ["1", "2", "3"],
        ["4", "5", "6"],
        ["7", "8", "9"],
        ["清空", "0", "删除"],
        ["✅ 提交", "🔄 重发验证码"],
        ["⬅️ 取消绑定"],
    ]


def test_account_home_buttons_are_rendered_in_readable_rows():
    from tg_v_chat.bot.router import BotResponse, ButtonSpec

    response = BotResponse(
        "账号管理",
        buttons=(
            ButtonSpec("重新登录", "account.relogin:1"),
            ButtonSpec("绑定 TG 账号", "account.bind.start"),
            ButtonSpec("我的账号", "account.list"),
            ButtonSpec("授权状态", "account.status"),
            ButtonSpec("中转说明", "account.relay_help"),
            ButtonSpec("帮助", "account.help"),
        ),
    )
    rows = _buttons(response)

    assert [[button.text for button in row] for row in rows] == [
        ["重新登录", "绑定 TG 账号"],
        ["我的账号", "授权状态"],
        ["中转说明", "帮助"],
    ]


def test_bot_router_handles_replies_through_handler():
    commands = []
    router = BotUpdateRouter(commands.append, FakeAccountManagement())

    responses = router.handle(BotIncomingMessage(146517, 12, 500, "收到"))

    assert commands[0].system_user_id == 146517
    assert commands[0].bot_reply_message_id == 12
    assert commands[0].reply_to_message_id == 500
    assert commands[0].payload == "收到"
    assert responses == []


def test_bot_role_runs_bot_process_instead_of_idle_wait():
    calls = []

    run_role("bot", bot_runner=lambda: calls.append("bot"), wait=lambda _role: calls.append("wait"))

    assert calls == ["bot"]


def test_listener_role_runs_listener_process_instead_of_idle_wait():
    calls = []

    run_role(
        "listener",
        listener_runner=lambda: calls.append("listener"),
        wait=lambda _role: calls.append("wait"),
    )

    assert calls == ["listener"]
