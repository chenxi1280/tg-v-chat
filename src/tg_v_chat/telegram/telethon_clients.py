from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotResponse, BotUpdateRouter
from tg_v_chat.domain import IncomingPrivateMessage, OutgoingReply, SessionFailure, SessionSlotRef
from tg_v_chat.services.auth import AuthChallenge, AuthFailure, AuthStep, PasswordRequired


@dataclass(frozen=True)
class DeveloperAppConfig:
    api_id: int
    api_hash: str


class TelethonBotGateway:
    def __init__(self, send_message: Callable[[int, IncomingPrivateMessage], int] | None = None):
        self._send_message = send_message

    def push_private_message(self, system_user_id: int, message: IncomingPrivateMessage) -> int:
        if self._send_message is None:
            raise RuntimeError("Telethon bot client is not connected")
        return self._send_message(system_user_id, message)

    def notify_failure(self, system_user_id: int, text: str) -> None:
        if self._send_message is None:
            raise RuntimeError("Telethon bot client is not connected")
        failure_message = IncomingPrivateMessage(0, system_user_id, 0, message_kind_text(), text, None, 0)
        self._send_message(system_user_id, failure_message)


class TelethonSenderPool:
    def __init__(self, send_reply: Callable[[SessionSlotRef, int, OutgoingReply], int] | None = None):
        self._send_reply = send_reply

    def send_reply(self, session_slot: SessionSlotRef, peer_id: int, reply: OutgoingReply) -> int:
        if session_slot.encrypted_session is None:
            raise SessionFailure(f"{session_slot.developer_slot.value} session 未授权")
        if self._send_reply is None:
            raise SessionFailure("Telethon user session client is not connected")
        return self._send_reply(session_slot, peer_id, reply)


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


def message_kind_text():
    from tg_v_chat.domain import MediaKind

    return MediaKind.TEXT


class TelethonBotProcess:
    def __init__(
        self,
        app_config: DeveloperAppConfig,
        bot_token: str,
        router: BotUpdateRouter,
    ):
        self._app_config = app_config
        self._bot_token = bot_token
        self._router = router

    def run(self) -> None:
        import asyncio

        asyncio.run(self._run())

    async def _run(self) -> None:
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), self._app_config.api_id, self._app_config.api_hash)
        await client.start(bot_token=self._bot_token)
        client.add_event_handler(self._handle_new_message, events.NewMessage(incoming=True))
        client.add_event_handler(self._handle_callback, events.CallbackQuery())
        print("tg-v-chat bot connected")
        await client.run_until_disconnected()

    async def _handle_new_message(self, event) -> None:
        if not getattr(event, "is_private", False):
            return
        import asyncio

        responses = await asyncio.to_thread(self._router.handle, _incoming_message(event))
        for response in responses:
            await event.reply(response.text, buttons=_buttons(response))

    async def _handle_callback(self, event) -> None:
        import asyncio

        callback = BotCallback(event.sender_id, event.data.decode("utf-8"))
        responses = await asyncio.to_thread(self._router.handle_callback, callback)
        for response in responses:
            await _send_callback_response(event, response)
        await event.answer()


def _incoming_message(event) -> BotIncomingMessage:
    message = event.message
    return BotIncomingMessage(
        system_user_id=event.sender_id,
        message_id=message.id,
        reply_to_message_id=_reply_to_message_id(message),
        text=event.raw_text or "",
        media_kind=message_kind_text(),
    )


def _reply_to_message_id(message) -> int | None:
    reply = getattr(message, "reply_to", None)
    if reply is None:
        return None
    return getattr(reply, "reply_to_msg_id", None)


CODE_KEYPAD_ROW_WIDTHS = (3, 3, 3, 3, 2, 1)
DEFAULT_BUTTON_ROW_WIDTH = 2


def _buttons(response: BotResponse):
    if not response.buttons:
        return None
    from telethon import Button

    buttons = [Button.inline(button.text, button.data.encode("utf-8")) for button in response.buttons]
    if _is_code_keypad(response.buttons):
        return _chunk_buttons(buttons, CODE_KEYPAD_ROW_WIDTHS)
    return _chunk_buttons_by_width(buttons, DEFAULT_BUTTON_ROW_WIDTH)


def _is_code_keypad(buttons) -> bool:
    expected_count = sum(CODE_KEYPAD_ROW_WIDTHS)
    return len(buttons) == expected_count and any(button.data.startswith("account.code.") for button in buttons)


def _chunk_buttons(buttons, row_widths: tuple[int, ...]):
    rows = []
    cursor = 0
    for width in row_widths:
        rows.append(buttons[cursor : cursor + width])
        cursor += width
    return rows


def _chunk_buttons_by_width(buttons, width: int):
    return [buttons[index : index + width] for index in range(0, len(buttons), width)]


async def _send_callback_response(event, response: BotResponse) -> None:
    if response.edit_message:
        await event.edit(response.text, buttons=_buttons(response))
        return
    await event.respond(response.text, buttons=_buttons(response))


def _run_async(coro):
    import asyncio

    return asyncio.run(coro)
