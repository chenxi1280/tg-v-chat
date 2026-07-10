from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from tg_v_chat.bot.account_management import AccountManagementService
from tg_v_chat.bot.handlers import BotReplyHandler
from tg_v_chat.bot.router import BotUpdateRouter
from tg_v_chat.config import load_config
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.services.auth import TelegramAuthenticator
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.database import create_session_factory, require_postgresql_url
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.telegram.telethon_clients import TelethonBotGateway, TelethonReplySender, TelethonSenderPool
from tg_v_chat.workers.runner import WorkerRunner
from tg_v_chat.workers.session_health import SessionHealthWorker, TelethonSessionVerifier


@dataclass(frozen=True)
class Runtime:
    bot_handler: BotReplyHandler
    bot_gateway: TelethonBotGateway
    sender_pool: TelethonSenderPool
    worker_runner: WorkerRunner
    session_factory: object
    session_cipher: SessionCipher

    def bot_router(self, authenticator: TelegramAuthenticator) -> BotUpdateRouter:
        account_management = AccountManagementService(self.session_factory, authenticator, self.session_cipher)
        return BotUpdateRouter(self.bot_handler.handle_reply, account_management)


def build_runtime(
    database_url: str,
    session_key: str,
    bot_token: str,
    *,
    app_configs: dict | None = None,
    allow_sqlite_for_tests: bool = False,
    media_root: str | None = None,
) -> Runtime:
    if not bot_token:
        raise RuntimeError("bot token is required")
    if not allow_sqlite_for_tests:
        require_postgresql_url(database_url)
    cipher = SessionCipher(session_key)
    session_factory = create_session_factory(database_url)
    media_store = MediaStore(media_root) if media_root is not None else None
    bot_gateway = TelethonBotGateway()
    sender_pool = _sender_pool(app_configs, cipher, media_store)
    worker_runner = _worker_runner(app_configs, session_factory, cipher, media_store=media_store)
    handler = BotReplyHandler(_relay_factory(session_factory, bot_gateway, sender_pool, media_store=media_store))
    return Runtime(handler, bot_gateway, sender_pool, worker_runner, session_factory, cipher)


def main() -> None:
    config = load_config()
    build_runtime(config.database_url, config.session_encryption_key, config.bot_token)


def _relay_factory(session_factory, bot_gateway, sender_pool, *, media_store):
    @contextmanager
    def create_service() -> PrivateRelayService:
        with UnitOfWork(session_factory) as unit:
            yield PrivateRelayService(unit, bot_gateway, sender_pool, media_store=media_store)

    return create_service


def _sender_pool(app_configs: dict | None, cipher: SessionCipher, media_store) -> TelethonSenderPool:
    if app_configs is None:
        return TelethonSenderPool()
    reply_sender = TelethonReplySender(app_configs, cipher, media_store=media_store)
    return TelethonSenderPool(reply_sender.send_reply)


def _worker_runner(app_configs: dict | None, session_factory, cipher: SessionCipher, *, media_store) -> WorkerRunner:
    if app_configs is None:
        return WorkerRunner()
    worker = SessionHealthWorker(session_factory, cipher, TelethonSessionVerifier(app_configs), media_store=media_store)
    return WorkerRunner([worker.run_once])
