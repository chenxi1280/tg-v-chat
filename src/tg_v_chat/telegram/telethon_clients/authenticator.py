from __future__ import annotations

from tg_v_chat.services.auth import AuthChallenge, AuthFailure, PasswordRequired
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
            return client.session.save()
        except SessionPasswordNeededError:
            return PasswordRequired(client.session.save())
        except (PhoneCodeExpiredError, PhoneCodeHashEmptyError) as exc:
            raise AuthFailure("验证码已过期，请重新开始绑定。", restart_required=True) from exc
        except (PhoneCodeEmptyError, PhoneCodeInvalidError) as exc:
            raise AuthFailure("验证码不正确，请检查后重新输入。") from exc
        finally:
            await client.disconnect()

    async def _sign_in_with_password(self, partial_session: str, password: str) -> str:
        from telethon import TelegramClient
        from telethon.errors import PasswordHashInvalidError
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(partial_session), self._app_config.api_id, self._app_config.api_hash)
        await client.connect()
        try:
            await client.sign_in(password=password)
            return client.session.save()
        except PasswordHashInvalidError as exc:
            raise AuthFailure("二次密码不正确，请重新输入。") from exc
        finally:
            await client.disconnect()
