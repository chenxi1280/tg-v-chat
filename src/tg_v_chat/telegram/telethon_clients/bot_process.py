from __future__ import annotations

import asyncio

from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotResponse, BotUpdateRouter
from tg_v_chat.telegram.telethon_clients.config import DeveloperAppConfig, message_kind_text

CODE_KEYPAD_ROW_WIDTHS = (3, 3, 3, 3, 2, 1)
DEFAULT_BUTTON_ROW_WIDTH = 2


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
        responses = await asyncio.to_thread(self._router.handle, _incoming_message(event))
        for response in responses:
            await event.reply(response.text, buttons=_buttons(response))

    async def _handle_callback(self, event) -> None:
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
