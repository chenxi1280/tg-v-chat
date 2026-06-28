import pytest

from tg_v_chat.bot.handlers import BotReplyHandler
from tg_v_chat.config import load_config
from tg_v_chat.main import build_runtime
from tg_v_chat.telegram.telethon_clients import TelethonBotGateway, TelethonSenderPool
from tg_v_chat.workers.runner import WorkerRunner


def test_load_config_requires_secrets(monkeypatch):
    for name in ("TG_V_CHAT_DATABASE_URL", "TG_V_CHAT_SESSION_KEY", "TG_V_CHAT_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="TG_V_CHAT_DATABASE_URL"):
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
