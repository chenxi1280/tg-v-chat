from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace

from sqlalchemy.exc import IntegrityError

from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeliveryFailure, DeveloperSlot, IncomingPrivateBatch, IncomingPrivateMessage, SessionStatus
from tg_v_chat.runtime_health import RoleHeartbeat
from tg_v_chat.services.native_forward import (
    NativeForwardCollector,
    NativeForwardDispatchService,
    NativeForwardReconciliationService,
)
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.models import utc_now
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.telegram.private_listener.event_parsing import (
    async_native_forward_batch_from_album,
    async_native_forward_message_from_event,
    async_private_batch_from_album,
    async_private_message_from_event,
)
from tg_v_chat.telegram.private_listener.formatting import _format_push_message
from tg_v_chat.telegram.private_listener.native_forward import TelethonUserSessionForwarder
from tg_v_chat.telegram.telethon_clients import DeveloperAppConfig, TelethonBotGateway

LISTENER_REFRESH_SECONDS = 10


@dataclass(frozen=True)
class BoundListenerSession:
    account_id: int
    system_user_id: int
    phone_number: str
    display_name: str | None
    username: str | None
    developer_slot: str
    session_string: str
    slot_id: int = 0
    session_revision: int = 0
    telegram_user_id: int | None = None

    @property
    def fingerprint(self) -> tuple[int, int, str, int, int | None]:
        return (self.account_id, self.slot_id, self.developer_slot, self.session_revision, self.telegram_user_id)


@dataclass(frozen=True)
class ListenerClientState:
    client: object
    fingerprint: tuple


class TelethonPrivateListenerProcess:
    def __init__(
        self,
        app_configs: dict[DeveloperSlot, DeveloperAppConfig],
        bot_token: str,
        session_factory,
        *,
        session_cipher: SessionCipher,
        media_root: str | None = None,
        heartbeat: RoleHeartbeat | None = None,
        native_forward_v2_enabled: bool = False,
        bot_username: str | None = None,
        native_forward_bridge_timeout_seconds: int = 30,
    ):
        self._app_configs = app_configs
        self._bot_token = bot_token
        self._session_factory = session_factory
        self._session_cipher = session_cipher
        self._media_store = MediaStore(media_root) if media_root is not None else None
        self._clients: dict[int, ListenerClientState] = {}
        self._heartbeat = heartbeat
        self._native_forward_v2_enabled = native_forward_v2_enabled
        self._bot_username = bot_username
        self._native_forward_bridge_timeout_seconds = native_forward_bridge_timeout_seconds

    def run(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        bot_client = await self._start_bot_client()
        bot_gateway = _bot_gateway(asyncio.get_running_loop(), bot_client, self._media_store)
        try:
            while True:
                await self._sync_clients(bot_gateway)
                if self._heartbeat is not None:
                    self._heartbeat.beat("listener")
                await asyncio.sleep(LISTENER_REFRESH_SECONDS)
        finally:
            await self._disconnect_clients()
            await bot_client.disconnect()

    async def _start_bot_client(self):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        app_config = self._app_configs[DeveloperSlot.PRIMARY]
        client = TelegramClient(StringSession(), app_config.api_id, app_config.api_hash)
        await client.start(bot_token=self._bot_token)
        print("tg-v-chat listener bot client connected")
        return client

    async def _sync_clients(self, bot_gateway: TelethonBotGateway) -> None:
        bindings = _load_active_bindings(self._session_factory, self._session_cipher)
        if self._native_forward_v2_enabled:
            _require_v2_identity_readiness(self._session_factory, self._bot_username)
            _require_v2_binding_identities(bindings, self._bot_username)
        active_ids = {binding.account_id for binding in bindings}
        await self._disconnect_removed_clients(active_ids)
        for binding in bindings:
            current = self._clients.get(binding.account_id)
            if current and current.fingerprint == binding.fingerprint:
                continue
            if current:
                await self._disconnect_client(binding.account_id)
            try:
                client = await self._start_user_client(binding, bot_gateway)
            except Exception as exc:
                print(f"tg-v-chat listener start failed for account {binding.account_id}: {exc}")
                continue
            self._clients[binding.account_id] = ListenerClientState(client, binding.fingerprint)
        if self._native_forward_v2_enabled:
            await self._recover_v2_batches(bindings, bot_gateway)

    async def _start_user_client(self, binding: BoundListenerSession, bot_gateway: TelethonBotGateway):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        app_config = self._app_configs[DeveloperSlot(binding.developer_slot)]
        client = TelegramClient(StringSession(binding.session_string), app_config.api_id, app_config.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(f"TG 账号未授权，无法监听: {binding.phone_number}")
        self._register_listener_handlers(client, binding, bot_gateway)
        _schedule_bound_account_identity_sync(client, binding, self._session_factory)
        print(f"tg-v-chat listener connected account {binding.account_id} {binding.developer_slot}")
        return client

    def _register_listener_handlers(self, client, binding, bot_gateway) -> None:
        if not self._native_forward_v2_enabled:
            _register_v1_handlers(client, binding, self._session_factory, bot_gateway, self._media_store)
            return
        forwarder = TelethonUserSessionForwarder(client, self._bot_username, loop=asyncio.get_running_loop())
        _register_v2_handlers(
            client,
            binding,
            self._session_factory,
            forwarder,
            self._native_forward_bridge_timeout_seconds,
            bot_gateway,
        )

    async def _recover_v2_batches(self, bindings: list[BoundListenerSession], notifier) -> None:
        await asyncio.to_thread(_reconcile_native_forwards, self._session_factory, notifier)
        loop = asyncio.get_running_loop()
        for binding in bindings:
            state = self._clients.get(binding.account_id)
            if state is None:
                continue
            forwarder = TelethonUserSessionForwarder(state.client, self._bot_username, loop=loop)
            try:
                await asyncio.to_thread(
                    _dispatch_native_due,
                    self._session_factory,
                    binding.account_id,
                    forwarder,
                    self._native_forward_bridge_timeout_seconds,
                    notifier,
                )
            except Exception as exc:
                print(f"tg-v-chat native forward recovery failed for account {binding.account_id}: {exc}")

    async def _disconnect_removed_clients(self, active_ids: set[int]) -> None:
        removed_ids = [account_id for account_id in self._clients if account_id not in active_ids]
        for account_id in removed_ids:
            await self._disconnect_client(account_id)

    async def _disconnect_client(self, account_id: int) -> None:
        state = self._clients.pop(account_id)
        await state.client.disconnect()
        print(f"tg-v-chat listener disconnected account {account_id}")

    async def _disconnect_clients(self) -> None:
        for account_id, client in list(self._clients.items()):
            await client.client.disconnect()
            print(f"tg-v-chat listener disconnected account {account_id}")
        self._clients.clear()


def private_message_event_builder():
    from telethon import events

    return events.NewMessage(incoming=True)


def private_album_event_builder():
    from telethon import events

    return events.Album()


def _register_v1_handlers(client, binding, session_factory, bot_gateway, media_store) -> None:
    client.add_event_handler(
        _incoming_handler(binding, session_factory, bot_gateway, media_store=media_store),
        private_message_event_builder(),
    )
    client.add_event_handler(
        _album_handler(binding, session_factory, bot_gateway, media_store=media_store),
        private_album_event_builder(),
    )


def _register_v2_handlers(client, binding, session_factory, forwarder, bridge_timeout_seconds: int, notifier) -> None:
    client.add_event_handler(
        _native_incoming_handler(binding, session_factory, forwarder, bridge_timeout_seconds, notifier),
        private_message_event_builder(),
    )
    client.add_event_handler(
        _native_album_handler(binding, session_factory, forwarder, bridge_timeout_seconds, notifier),
        private_album_event_builder(),
    )


def _incoming_handler(binding: BoundListenerSession, session_factory, bot_gateway: TelethonBotGateway, *, media_store):
    async def handle(event) -> None:
        if not getattr(event, "is_private", False):
            return
        message = await async_private_message_from_event(binding, event, media_store=media_store)
        if message is None:
            return
        await asyncio.to_thread(_receive_message, session_factory, bot_gateway, message, media_store=media_store)

    return handle


def _album_handler(binding: BoundListenerSession, session_factory, bot_gateway: TelethonBotGateway, *, media_store):
    async def handle(event) -> None:
        if not getattr(event, "is_private", False):
            return
        batch = await async_private_batch_from_album(binding, event, media_store=media_store)
        await asyncio.to_thread(_receive_batch, session_factory, bot_gateway, batch, media_store=media_store)

    return handle


def _native_incoming_handler(binding, session_factory, forwarder, bridge_timeout_seconds: int, notifier):
    async def handle(event) -> None:
        if not getattr(event, "is_private", False):
            return
        message = await async_native_forward_message_from_event(binding, event)
        if message is None:
            return
        collected = await asyncio.to_thread(_receive_native_message, session_factory, message)
        _schedule_native_dispatch(
            session_factory,
            binding.account_id,
            forwarder,
            bridge_timeout_seconds,
            notifier,
            delay=1,
        )
        return collected

    return handle


def _native_album_handler(binding, session_factory, forwarder, bridge_timeout_seconds: int, notifier):
    async def handle(event) -> None:
        if not getattr(event, "is_private", False):
            return
        batch = await async_native_forward_batch_from_album(binding, event)
        await asyncio.to_thread(_receive_native_batch, session_factory, batch)
        _schedule_native_dispatch(
            session_factory,
            binding.account_id,
            forwarder,
            bridge_timeout_seconds,
            notifier,
            delay=0,
        )

    return handle


def _receive_message(session_factory, bot_gateway: TelethonBotGateway, message: IncomingPrivateMessage, *, media_store) -> None:
    with UnitOfWork(session_factory) as uow:
        service = PrivateRelayService(uow, bot_gateway, _NoopSenderPool(), media_store=media_store)
        service.receive_private_message(message)


def _receive_batch(session_factory, bot_gateway: TelethonBotGateway, batch: IncomingPrivateBatch, *, media_store) -> None:
    with UnitOfWork(session_factory) as uow:
        service = PrivateRelayService(uow, bot_gateway, _NoopSenderPool(), media_store=media_store)
        service.receive_private_batch(batch)


def _receive_native_message(session_factory, message: IncomingPrivateMessage):
    with UnitOfWork(session_factory) as uow:
        return NativeForwardCollector(uow).ingest(message, now=utc_now())


def _receive_native_batch(session_factory, batch: IncomingPrivateBatch):
    with UnitOfWork(session_factory) as uow:
        return NativeForwardCollector(uow).ingest_batch(batch, now=utc_now())


def _schedule_native_dispatch(session_factory, account_id: int, forwarder, bridge_timeout_seconds: int, notifier, *, delay: int):
    async def dispatch() -> None:
        if delay:
            await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(
                _reconcile_and_dispatch_native,
                session_factory,
                account_id,
                forwarder,
                bridge_timeout_seconds,
                notifier,
            )
        except Exception as exc:
            print(f"tg-v-chat native forward dispatch failed for account {account_id}: {exc}")

    asyncio.create_task(dispatch())


def _reconcile_and_dispatch_native(session_factory, account_id: int, forwarder, bridge_timeout_seconds: int, notifier) -> None:
    _reconcile_native_forwards(session_factory, notifier)
    _dispatch_native_due(session_factory, account_id, forwarder, bridge_timeout_seconds, notifier)


def _reconcile_native_forwards(session_factory, notifier) -> None:
    NativeForwardReconciliationService(session_factory, notifier=notifier).expire(now=utc_now())


def _dispatch_native_due(session_factory, account_id: int, forwarder, bridge_timeout_seconds: int, notifier) -> None:
    NativeForwardDispatchService(
        session_factory,
        forwarder,
        bridge_timeout_seconds=bridge_timeout_seconds,
        notifier=notifier,
    ).dispatch_due(account_id)


def _load_active_bindings(session_factory, cipher: SessionCipher) -> list[BoundListenerSession]:
    with UnitOfWork(session_factory) as uow:
        return _bindings_from_accounts(uow.accounts.list_operational(), uow, cipher)


def _require_v2_binding_identities(bindings: list[BoundListenerSession], bot_username: str | None) -> None:
    if not bot_username:
        raise RuntimeError("bot_username_missing: V2 原生转发缺少 Bot username")
    missing = [str(binding.account_id) for binding in bindings if binding.telegram_user_id is None]
    if missing:
        raise RuntimeError(f"bound_account_identity_missing: {','.join(missing)}")


def _require_v2_identity_readiness(session_factory, bot_username: str | None) -> None:
    if not bot_username:
        raise RuntimeError("bot_username_missing: V2 原生转发缺少 Bot username")
    with UnitOfWork(session_factory) as uow:
        missing = uow.accounts.list_operational_without_telegram_identity()
    if missing:
        account_ids = ",".join(str(account.id) for account in missing)
        raise RuntimeError(f"bound_account_identity_missing: {account_ids}")


def _bindings_from_accounts(accounts, uow, cipher: SessionCipher) -> list[BoundListenerSession]:
    bindings = []
    for account in accounts:
        binding = _binding_from_account(account, uow, cipher)
        if binding is not None:
            bindings.append(binding)
    return bindings


def _binding_from_account(account, uow, cipher: SessionCipher) -> BoundListenerSession | None:
    slot = _listener_slot(uow.sessions.list_for_account(account.id))
    if slot is None:
        return None
    return BoundListenerSession(
        account_id=account.id,
        system_user_id=account.system_user_id,
        phone_number=account.phone_number,
        display_name=account.display_name,
        username=account.username,
        developer_slot=slot.developer_slot,
        session_string=cipher.decrypt(slot.encrypted_session),
        slot_id=slot.id,
        session_revision=slot.revision,
        telegram_user_id=account.telegram_user_id,
    )


def _listener_slot(slots) -> object | None:
    for slot in slots:
        if slot.status == SessionStatus.ACTIVE.value and slot.encrypted_session:
            return slot
    for slot in slots:
        if slot.status == SessionStatus.STANDBY.value and slot.encrypted_session:
            return slot
    return None


async def _sync_bound_account_identity(client, binding: BoundListenerSession, session_factory):
    identity = _identity_from_user(await client.get_me())
    try:
        with UnitOfWork(session_factory) as uow:
            with uow.account_locks.acquire(binding.account_id):
                account = uow.accounts.get(binding.account_id)
                if account.status == "deleted":
                    raise DeliveryFailure("account_unavailable", "账号已删除，拒绝回填 Telegram identity")
                with uow.telegram_identity_locks.acquire(identity.telegram_user_id):
                    _require_identity_available(uow, account.id, identity.telegram_user_id)
                    uow.accounts.update_telegram_identity(account.id, identity.telegram_user_id)
                    uow.accounts.update_profile(
                        account.id,
                        display_name=identity.display_name,
                        username=identity.username,
                    )
                    uow.commit()
    except IntegrityError as exc:
        raise DeliveryFailure("bound_account_already_bound", "该 Telegram 账号已绑定到其他账号") from exc
    return replace(
        binding,
        telegram_user_id=identity.telegram_user_id,
        display_name=identity.display_name,
        username=identity.username,
    )


def _require_identity_available(uow, account_id: int, telegram_user_id: int) -> None:
    existing = uow.accounts.get_by_telegram_identity(telegram_user_id)
    if existing is not None and existing.id != account_id:
        raise DeliveryFailure("bound_account_already_bound", "该 Telegram 账号已绑定到其他账号")


@dataclass(frozen=True)
class TelegramAccountIdentity:
    telegram_user_id: int
    display_name: str | None
    username: str | None


def _identity_from_user(user) -> TelegramAccountIdentity:
    if user is None or getattr(user, "id", None) is None:
        raise RuntimeError("Telegram get_me() did not return an account id")
    display_name, username = _profile_from_user(user)
    return TelegramAccountIdentity(int(user.id), display_name, username)


def _profile_from_user(user) -> tuple[str | None, str | None]:
    if user is None:
        return None, None
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    display_name = " ".join(part for part in parts if part) or getattr(user, "username", None)
    return display_name, getattr(user, "username", None)


def _schedule_bound_account_identity_sync(client, binding: BoundListenerSession, session_factory) -> None:
    async def sync() -> None:
        try:
            await _sync_bound_account_identity(client, binding, session_factory)
        except Exception as exc:
            print(f"tg-v-chat listener identity sync failed for account {binding.account_id}: {exc}")

    asyncio.create_task(sync())


def _bot_sender(loop, bot_client) -> Callable[[int, IncomingPrivateMessage], int]:
    def send(system_user_id: int, message: IncomingPrivateMessage) -> int:
        future = asyncio.run_coroutine_threadsafe(
            bot_client.send_message(system_user_id, _format_push_message(message)),
            loop,
        )
        return future.result().id

    return send


def _bot_gateway(loop, bot_client, media_store):
    if media_store is not None:
        return TelethonBotGateway.from_client(bot_client, media_store, loop=loop)
    return TelethonBotGateway(_bot_sender(loop, bot_client))


class _NoopSenderPool:
    def send_reply(self, _session_slot, _peer, _reply) -> int:
        raise RuntimeError("listener process does not send replies")
