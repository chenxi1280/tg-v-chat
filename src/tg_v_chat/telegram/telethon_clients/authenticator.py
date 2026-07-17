from __future__ import annotations

from collections.abc import Callable, Mapping

from tg_v_chat.domain import DeveloperSlot
from tg_v_chat.services.auth import AuthChallenge, AuthFailure, AuthenticatedSession, PasswordRequired
from tg_v_chat.telegram.telethon_clients.config import DeveloperAppConfig
from tg_v_chat.telegram.telethon_clients.helpers import _run_async


class TelethonAuthenticator:
    def __init__(self, app_config: DeveloperAppConfig):
        self._app_config = app_config

    def start(self, phone_number, slot):
        code_hash, pending_session = _run_async(self._send_code(phone_number))
        return AuthChallenge(phone_number, slot, code_hash, pending_session=pending_session)

    def complete_code(self, challenge, code):
        return _run_async(self._sign_in_with_code(challenge, code))

    def complete_password(self, challenge, password):
        if challenge.pending_session is None:
            raise AuthFailure("当前 2FA 登录会话已失效，请重新开始绑定。", restart_required=True)
        return _run_async(self._sign_in_with_password(challenge.pending_session, password))

    async def _send_code(self, phone_number: str) -> tuple[str, str]:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), self._app_config.api_id, self._app_config.api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(phone_number)
            return sent.phone_code_hash, client.session.save()
        finally:
            await client.disconnect()

    async def _sign_in_with_code(self, challenge: AuthChallenge, code: str):
        from telethon import TelegramClient
        from telethon.errors import (
            PhoneCodeEmptyError,
            PhoneCodeExpiredError,
            PhoneCodeHashEmptyError,
            PhoneCodeInvalidError,
            SessionPasswordNeededError,
        )
        from telethon.sessions import StringSession

        if challenge.pending_session is None:
            raise AuthFailure("当前验证码登录会话已失效，请重新开始绑定。", restart_required=True)

        client = TelegramClient(
            StringSession(challenge.pending_session),
            self._app_config.api_id,
            self._app_config.api_hash,
        )
        await client.connect()
        try:
            await client.sign_in(challenge.phone_number, code, phone_code_hash=challenge.phone_code_hash)
            return await _authenticated_session(client)
        except SessionPasswordNeededError:
            return PasswordRequired(client.session.save())
        except (PhoneCodeExpiredError, PhoneCodeHashEmptyError) as exc:
            raise AuthFailure("验证码已过期，请重新开始绑定。", restart_required=True) from exc
        except (PhoneCodeEmptyError, PhoneCodeInvalidError) as exc:
            raise AuthFailure("验证码不正确，请检查后重新输入。") from exc
        finally:
            await client.disconnect()

    async def _sign_in_with_password(self, partial_session: str, password: str) -> AuthenticatedSession:
        from telethon import TelegramClient
        from telethon.errors import PasswordHashInvalidError
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(partial_session), self._app_config.api_id, self._app_config.api_hash)
        await client.connect()
        try:
            await client.sign_in(password=password)
            return await _authenticated_session(client)
        except PasswordHashInvalidError as exc:
            raise AuthFailure("二次密码不正确，请重新输入。") from exc
        finally:
            await client.disconnect()


class SlotAuthenticatorRegistry:
    def __init__(
        self,
        app_configs: Mapping[DeveloperSlot, DeveloperAppConfig],
        *,
        authenticator_factory: Callable[[DeveloperAppConfig], TelethonAuthenticator] = TelethonAuthenticator,
    ):
        missing = set(DeveloperSlot) - set(app_configs)
        if missing:
            names = ", ".join(sorted(slot.value for slot in missing))
            raise ValueError(f"缺少 developer app 配置: {names}")
        self._authenticators = {slot: authenticator_factory(app_configs[slot]) for slot in DeveloperSlot}

    def start(self, phone_number: str, slot: DeveloperSlot) -> AuthChallenge:
        return self._for_slot(slot).start(phone_number, slot)

    def complete_code(self, challenge: AuthChallenge, code: str):
        return self._for_slot(challenge.developer_slot).complete_code(challenge, code)

    def complete_password(self, challenge: AuthChallenge, password: str):
        return self._for_slot(challenge.developer_slot).complete_password(challenge, password)

    def _for_slot(self, slot: DeveloperSlot) -> TelethonAuthenticator:
        try:
            return self._authenticators[slot]
        except KeyError as exc:
            raise ValueError(f"未知 developer slot: {slot}") from exc


async def _authenticated_session(client) -> AuthenticatedSession:
    me = await client.get_me()
    return AuthenticatedSession(
        session_string=client.session.save(),
        telegram_user_id=_telegram_user_id(me),
        display_name=_display_name(me),
        username=getattr(me, "username", None),
    )


def _display_name(user) -> str | None:
    if user is None:
        return None
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    name = " ".join(part for part in parts if part)
    return name or getattr(user, "username", None)


def _telegram_user_id(user) -> int:
    telegram_user_id = getattr(user, "id", None)
    if not isinstance(telegram_user_id, int) or telegram_user_id <= 0:
        raise AuthFailure("Telegram 未返回有效账号 identity，请重新开始绑定。", restart_required=True)
    return telegram_user_id
