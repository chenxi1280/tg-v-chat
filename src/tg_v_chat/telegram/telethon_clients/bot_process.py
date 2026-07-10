from __future__ import annotations

import asyncio

from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotResponse, BotUpdateRouter
from tg_v_chat.domain import MediaKind
from tg_v_chat.runtime_health import heartbeat_loop
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.telegram.telethon_clients.config import DeveloperAppConfig, message_kind_text

CODE_KEYPAD_ROW_WIDTHS = (3, 3, 3, 3, 2, 1)
DEFAULT_BUTTON_ROW_WIDTH = 2


class TelethonBotProcess:
    def __init__(
        self,
        app_config: DeveloperAppConfig,
        bot_token: str,
        router: BotUpdateRouter,
        *,
        media_root: str | None = None,
        heartbeat=None,
    ):
        self._app_config = app_config
        self._bot_token = bot_token
        self._router = router
        self._media_store = MediaStore(media_root) if media_root is not None else None
        self._heartbeat = heartbeat

    def run(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), self._app_config.api_id, self._app_config.api_hash)
        await client.start(bot_token=self._bot_token)
        client.add_event_handler(self._handle_new_message, events.NewMessage(incoming=True))
        client.add_event_handler(self._handle_album, events.Album())
        client.add_event_handler(self._handle_callback, events.CallbackQuery())
        print("tg-v-chat bot connected")
        task = _start_heartbeat(self._heartbeat, "bot")
        try:
            await client.run_until_disconnected()
        finally:
            _cancel_heartbeat(task)

    async def _handle_new_message(self, event) -> None:
        if not getattr(event, "is_private", False):
            return
        incoming = await incoming_message_from_event(event, self._media_store)
        responses = await asyncio.to_thread(self._router.handle, incoming)
        for response in responses:
            await event.reply(response.text, buttons=_buttons(response))

    async def _handle_album(self, event) -> None:
        if not getattr(event, "is_private", False):
            return
        incoming = await incoming_album_from_event(event, self._media_store)
        responses = await asyncio.to_thread(self._router.handle, incoming)
        for response in responses:
            await event.reply(response.text, buttons=_buttons(response))

    async def _handle_callback(self, event) -> None:
        callback = BotCallback(event.sender_id, event.data.decode("utf-8"))
        responses = await asyncio.to_thread(self._router.handle_callback, callback)
        for response in responses:
            await _send_callback_response(event, response)
        await event.answer()


async def incoming_message_from_event(event, media_store=None) -> BotIncomingMessage:
    message = event.message
    media_kind = _media_kind(message)
    artifacts = await _reply_artifacts(message, media_store, media_kind)
    return BotIncomingMessage(
        system_user_id=event.sender_id,
        message_id=message.id,
        reply_to_message_id=_reply_to_message_id(message),
        text=event.raw_text or "",
        media_kind=media_kind,
        artifacts=artifacts,
    )


def _incoming_message(event) -> BotIncomingMessage:
    return BotIncomingMessage(
        system_user_id=event.sender_id,
        message_id=event.message.id,
        reply_to_message_id=_reply_to_message_id(event.message),
        text=event.raw_text or "",
        media_kind=message_kind_text(),
    )


async def incoming_album_from_event(event, media_store) -> BotIncomingMessage:
    messages = sorted(event.messages, key=lambda item: item.id)
    if not messages:
        raise RuntimeError("empty Bot reply album")
    artifacts = []
    for index, message in enumerate(messages, start=1):
        artifacts.extend(await _reply_artifacts(message, media_store, _media_kind(message), sequence=index))
    return BotIncomingMessage(
        system_user_id=event.sender_id,
        message_id=messages[0].id,
        reply_to_message_id=_reply_to_message_id(messages[0]),
        text=getattr(event, "raw_text", None) or "",
        media_kind=_media_kind(messages[0]),
        artifacts=tuple(artifacts),
    )


async def _reply_artifacts(message, media_store, media_kind: MediaKind, *, sequence: int = 1):
    if media_kind is MediaKind.TEXT:
        if getattr(message, "media", None):
            raise RuntimeError("unsupported Bot reply media")
        return ()
    if media_store is None:
        raise RuntimeError("media_store is required for Bot reply media")
    artifact = await media_store.download_message_media(
        message,
        file_name=_reply_file_name(message, media_kind),
        mime_type=_reply_mime_type(message, media_kind),
        media_kind=media_kind,
        sequence=sequence,
    )
    return (artifact,)


def _media_kind(message) -> MediaKind:
    if getattr(message, "photo", None) is not None:
        return MediaKind.PHOTO
    if getattr(message, "sticker", None) is not None:
        return MediaKind.STICKER
    return MediaKind.TEXT


def _reply_file_name(message, media_kind: MediaKind) -> str:
    file_meta = getattr(message, "file", None)
    name = getattr(file_meta, "name", None)
    if name:
        return name
    return f"bot-reply-{message.id}.{'webp' if media_kind is MediaKind.STICKER else 'jpg'}"


def _reply_mime_type(message, media_kind: MediaKind) -> str:
    file_meta = getattr(message, "file", None)
    mime = getattr(file_meta, "mime_type", None)
    if mime:
        return mime
    return "image/webp" if media_kind is MediaKind.STICKER else "image/jpeg"


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


def _start_heartbeat(heartbeat, role: str):
    if heartbeat is None:
        return None
    return asyncio.create_task(heartbeat_loop(heartbeat, role, asyncio.sleep))


def _cancel_heartbeat(task) -> None:
    if task is not None:
        task.cancel()
