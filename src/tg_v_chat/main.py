from __future__ import annotations

from dataclasses import dataclass

from tg_v_chat.bot.handlers import BotReplyHandler
from tg_v_chat.config import load_config
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.database import create_session_factory, require_postgresql_url
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.telethon_clients import TelethonBotGateway, TelethonSenderPool
from tg_v_chat.workers.runner import WorkerRunner


@dataclass(frozen=True)
class Runtime:
    bot_handler: BotReplyHandler
    bot_gateway: TelethonBotGateway
    sender_pool: TelethonSenderPool
    worker_runner: WorkerRunner


def build_runtime(
    database_url: str,
    session_key: str,
    bot_token: str,
    *,
    allow_sqlite_for_tests: bool = False,
) -> Runtime:
    if not bot_token:
        raise RuntimeError("bot token is required")
    if not allow_sqlite_for_tests:
        require_postgresql_url(database_url)
    SessionCipher(session_key)
    session_factory = create_session_factory(database_url)
    bot_gateway = TelethonBotGateway()
    sender_pool = TelethonSenderPool()
    handler = BotReplyHandler(_relay_factory(session_factory, bot_gateway, sender_pool))
    return Runtime(handler, bot_gateway, sender_pool, WorkerRunner())


def main() -> None:
    config = load_config()
    build_runtime(config.database_url, config.session_encryption_key, config.bot_token)


def _relay_factory(session_factory, bot_gateway, sender_pool):
    def create_service() -> PrivateRelayService:
        unit = UnitOfWork(session_factory).__enter__()
        return PrivateRelayService(unit, bot_gateway, sender_pool)

    return create_service
