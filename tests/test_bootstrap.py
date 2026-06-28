import pytest

from tg_v_chat.bot.handlers import BotReplyHandler
from tg_v_chat.bot.router import BotIncomingMessage, BotUpdateRouter
from tg_v_chat.config import load_config
from tg_v_chat.main import build_runtime
from tg_v_chat.runtime import run_role
from tg_v_chat.telegram.telethon_clients import TelethonBotGateway, TelethonSenderPool
from tg_v_chat.workers.runner import WorkerRunner


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
    replies = []
    router = BotUpdateRouter(
        lambda _command: None,
        replies.append,
    )

    router.handle(BotIncomingMessage(146517, 10, None, "/start"))
    router.handle(BotIncomingMessage(146517, 11, None, "/admin"))

    assert replies == [
        (10, "机器人在线。请直接回复 Bot 推送的私聊消息进行代发。"),
        (11, "机器人在线。当前未启用独立管理面板；绑定与中转失败会直接返回明确错误。"),
    ]


def test_bot_router_handles_replies_through_handler():
    commands = []
    replies = []
    router = BotUpdateRouter(commands.append, replies.append)

    router.handle(BotIncomingMessage(146517, 12, 500, "收到"))

    assert commands[0].system_user_id == 146517
    assert commands[0].bot_reply_message_id == 12
    assert commands[0].reply_to_message_id == 500
    assert commands[0].payload == "收到"
    assert replies == []


def test_bot_role_runs_bot_process_instead_of_idle_wait():
    calls = []

    run_role("bot", bot_runner=lambda: calls.append("bot"), wait=lambda _role: calls.append("wait"))

    assert calls == ["bot"]
