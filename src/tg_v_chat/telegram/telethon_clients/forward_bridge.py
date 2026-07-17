from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from tg_v_chat.domain import DeliveryFailure, DeliveryUncertain
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.storage.repositories.native_forward import BridgeItemMismatchError
from tg_v_chat.telegram.native_forward_protocol import is_marker_candidate, parse_marker


@dataclass(frozen=True)
class FinalBridgeDispatch:
    batch_id: int
    marker_token: str
    sender_telegram_user_id: int
    system_user_telegram_id: int
    bridge_message_ids: tuple[int, ...]
    header_text: str


class NativeForwardBridgeHandler:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._identity_locks = UnitOfWork(session_factory).telegram_identity_locks

    async def handle_message(self, event, *, allow_grouped: bool = False) -> bool:
        sender_id = getattr(event, "sender_id", None)
        if sender_id is None or not self._is_bound_sender(int(sender_id)):
            return False
        if _is_grouped_forward(event.message) and not allow_grouped:
            return True
        with self._identity_locks.acquire(int(sender_id)):
            return await self._handle_bound_sender(event, int(sender_id))

    async def handle_album(self, event) -> bool:
        if not getattr(event, "is_private", False):
            return False
        consumed = False
        for message in sorted(event.messages, key=lambda item: item.id):
            item_event = SimpleNamespace(
                sender_id=event.sender_id,
                raw_text=getattr(message, "message", None) or "",
                message=message,
                client=event.client,
            )
            consumed = await self.handle_message(item_event, allow_grouped=True) or consumed
        return consumed

    async def _handle_bound_sender(self, event, sender_id: int) -> bool:
        text = getattr(event, "raw_text", None)
        if is_marker_candidate(text):
            return self._consume_marker(event, sender_id)
        if not _is_forwarded(event.message):
            return False
        received = self._record_forwarded_item(sender_id, event.message)
        if received is None:
            self._record_quarantine(sender_id, event.message.id, None, "bridge_orphan_forward")
            return True
        if received.error_code is not None:
            self._record_quarantine(sender_id, event.message.id, received.marker_token, received.error_code)
            return True
        if received.complete:
            await self._finish_batch(event.client, received.marker_token)
        return True

    def _consume_marker(self, event, sender_id: int) -> bool:
        marker = parse_marker(getattr(event, "raw_text", None))
        if marker is None or not self._mark_awaiting(marker, sender_id, event.message.id):
            self._record_quarantine(sender_id, event.message.id, marker.token if marker else None, "bridge_marker_mismatch")
        return True

    def _is_bound_sender(self, sender_id: int) -> bool:
        with UnitOfWork(self._session_factory) as uow:
            return uow.accounts.get_by_telegram_identity(sender_id) is not None

    def _mark_awaiting(self, marker, sender_id: int, message_id: int) -> bool:
        with UnitOfWork(self._session_factory) as uow:
            batch = uow.native_forwards.get_by_marker(marker.token)
            if batch is None or batch.expected_count != marker.expected_count:
                return False
            stored = uow.native_forwards.mark_awaiting_bot(
                marker.token,
                sender_id,
                marker_message_id=message_id,
            )
            if stored is None:
                return False
            uow.commit()
            return True

    def _record_forwarded_item(self, sender_id: int, message) -> _BridgeReceive | None:
        with UnitOfWork(self._session_factory) as uow:
            active = uow.native_forwards.find_active_bridge(sender_id)
            if active is None:
                return None
            try:
                appended = uow.native_forwards.append_bridge_message(
                    sender_id,
                    message.id,
                    _identity_visibility(message),
                )
            except BridgeItemMismatchError:
                uow.commit()
                return _BridgeReceive(active.marker_token, False, "bridge_item_mismatch")
            except ValueError:
                uow.native_forwards.mark_uncertain(
                    active.id,
                    "bridge_item_count_mismatch",
                    "Bot 收到的桥接消息数量超过批次定义",
                )
                uow.commit()
                return _BridgeReceive(active.marker_token, False, "bridge_item_count_mismatch")
            uow.commit()
            return _BridgeReceive(appended.batch.marker_token, appended.complete, None)

    async def _finish_batch(self, client, marker_token: str) -> None:
        dispatch = self._prepare_final(marker_token)
        if dispatch is None:
            return
        header_id = await self._send_header(client, dispatch)
        if header_id is None:
            return
        await self._forward_final_messages(client, dispatch, header_id)

    def _prepare_final(self, marker_token: str) -> FinalBridgeDispatch | None:
        with UnitOfWork(self._session_factory) as uow:
            batch = uow.native_forwards.claim_final(marker_token)
            if batch is None:
                uow.commit()
                return None
            uow.native_forwards.create_and_claim_final_pushes(batch.id)
            account = uow.accounts.get(batch.bound_tg_account_id)
            user = uow.users.get(batch.system_user_id)
            items = uow.native_forwards.list_items(batch.id)
            bridge_ids = tuple(item.bridge_message_id for item in items)
            dispatch = FinalBridgeDispatch(
                batch.id,
                batch.marker_token,
                batch.bridge_sender_telegram_user_id,
                user.telegram_user_id,
                bridge_ids,
                _header_text(account, batch.expected_count, has_name_only=_has_name_only_origin(items)),
            )
            uow.commit()
            return dispatch

    async def _send_header(self, client, dispatch: FinalBridgeDispatch) -> int | None:
        try:
            header = await client.send_message(dispatch.system_user_telegram_id, dispatch.header_text)
        except Exception as exc:
            await self._handle_remote_error(client, dispatch, None, exc)
            return None
        try:
            self._record_header(dispatch.marker_token, header.id)
        except Exception as exc:
            self._mark_terminal(dispatch.batch_id, "uncertain", "bridge_commit_failed", str(exc))
            await _show_terminal(client, dispatch, header.id, "bridge_commit_failed")
            return None
        return header.id

    async def _forward_final_messages(self, client, dispatch: FinalBridgeDispatch, header_id: int) -> None:
        try:
            forwarded = await client.forward_messages(
                dispatch.system_user_telegram_id,
                list(dispatch.bridge_message_ids),
                from_peer=dispatch.sender_telegram_user_id,
            )
        except Exception as exc:
            await self._handle_remote_error(client, dispatch, header_id, exc)
            return
        final_ids = _message_ids(forwarded)
        if len(final_ids) != len(dispatch.bridge_message_ids):
            self._mark_terminal(
                dispatch.batch_id,
                "uncertain",
                "bridge_item_count_mismatch",
                "第二跳返回消息数量与批次不一致",
            )
            await _show_terminal(client, dispatch, header_id, "bridge_item_count_mismatch")
            return
        try:
            self._record_final(dispatch.marker_token, final_ids)
        except Exception as exc:
            self._mark_terminal(dispatch.batch_id, "uncertain", "bridge_commit_failed", str(exc))
            await _show_terminal(client, dispatch, header_id, "bridge_commit_failed")

    async def _handle_remote_error(self, client, dispatch, header_id: int | None, error: Exception) -> None:
        classified = _bridge_error(error)
        status = "uncertain" if isinstance(classified, DeliveryUncertain) else "failed"
        self._mark_terminal(dispatch.batch_id, status, classified.code, classified.detail)
        await _show_terminal(client, dispatch, header_id, classified.code)

    def _record_header(self, marker_token: str, header_id: int) -> None:
        with UnitOfWork(self._session_factory) as uow:
            uow.native_forwards.record_header(marker_token, header_id)
            uow.commit()

    def _record_final(self, marker_token: str, final_ids: tuple[int, ...]) -> None:
        with UnitOfWork(self._session_factory) as uow:
            uow.native_forwards.mark_final_sent(marker_token, final_ids)
            uow.commit()

    def _mark_terminal(self, batch_id: int, status: str, code: str, detail: str) -> None:
        with UnitOfWork(self._session_factory) as uow:
            if status == "failed":
                uow.native_forwards.mark_failed(batch_id, code, detail)
            else:
                uow.native_forwards.mark_uncertain(batch_id, code, detail)
            uow.commit()

    def _record_quarantine(self, sender_id: int, message_id: int, token: str | None, code: str) -> None:
        with UnitOfWork(self._session_factory) as uow:
            uow.native_forwards.record_quarantine(sender_id, message_id, token, code)
            uow.commit()


@dataclass(frozen=True)
class _BridgeReceive:
    marker_token: str
    complete: bool
    error_code: str | None


def _is_forwarded(message) -> bool:
    return getattr(message, "fwd_from", None) is not None or getattr(message, "forward", None) is not None


def _is_grouped_forward(message) -> bool:
    return getattr(message, "grouped_id", None) is not None and _is_forwarded(message)


def _identity_visibility(message) -> str:
    header = getattr(message, "fwd_from", None) or getattr(message, "forward", None)
    if getattr(header, "from_id", None) is not None or getattr(header, "channel_post", None) is not None:
        return "linked"
    return "name_only"


def _header_text(account, expected_count: int, *, has_name_only: bool) -> str:
    username = f"@{account.username}" if account.username else "未设置"
    lines = [
        f"接收账号：{account.display_name or '未命名账号'}",
        f"接收用户名：{username}",
        f"本批消息：{expected_count} 条",
    ]
    if has_name_only:
        lines.append("来源提示：本批部分来源受 Telegram 隐私保护，仅能显示名称")
    lines.append("原生转发中…")
    return "\n".join(lines)


def _has_name_only_origin(items) -> bool:
    return any(item.identity_visibility == "name_only" for item in items)


def _message_ids(messages) -> tuple[int, ...]:
    if not isinstance(messages, (list, tuple)):
        messages = (messages,)
    return tuple(int(message.id) for message in messages)


def _bridge_error(error: Exception) -> DeliveryFailure | DeliveryUncertain:
    from telethon.errors import (
        ChatForwardsRestrictedError,
        ChatWriteForbiddenError,
        FloodWaitError,
        MessageIdInvalidError,
        PeerIdInvalidError,
        RPCError,
    )

    if isinstance(error, (DeliveryFailure, DeliveryUncertain)):
        return error
    if isinstance(error, (ConnectionError, OSError, TimeoutError)):
        return DeliveryUncertain("bridge_transport_unknown", f"第二跳结果未知: {error}")
    if isinstance(error, PeerIdInvalidError):
        return DeliveryFailure("native_forward_peer_invalid", str(error))
    if isinstance(error, (ChatForwardsRestrictedError, ChatWriteForbiddenError)):
        return DeliveryFailure("native_forward_restricted", str(error))
    if isinstance(error, MessageIdInvalidError):
        return DeliveryFailure("native_forward_source_missing", str(error))
    if isinstance(error, FloodWaitError):
        return DeliveryFailure("flood_wait", str(error))
    if isinstance(error, RPCError):
        return DeliveryFailure("native_forward_rpc_rejected", str(error))
    return DeliveryFailure("native_forward_final_failed", str(error))


async def _show_terminal(client, dispatch: FinalBridgeDispatch, header_id: int | None, code: str) -> None:
    text = f"{dispatch.header_text}\n原生转发结果：{code}"
    try:
        if header_id is None:
            await client.send_message(dispatch.system_user_telegram_id, f"原生转发失败：{code}")
            return
        editor = getattr(client, "edit_message", None)
        if editor is not None:
            await editor(dispatch.system_user_telegram_id, header_id, text)
        else:
            await client.send_message(dispatch.system_user_telegram_id, text)
    except Exception as exc:
        print(f"tg-v-chat bridge status notification failed for batch {dispatch.batch_id}: {exc}")
