from __future__ import annotations

import asyncio

from tg_v_chat.domain import DeliveryFailure, DeliveryUncertain, FirstHopForwardResult, NativeForwardRequest
from tg_v_chat.telegram.native_forward_protocol import (
    ForwardBridgeMarker,
    MAX_BRIDGE_ITEMS,
    MARKER_PREFIX,
    is_marker_candidate,
    marker_text,
    parse_marker,
)


class TelethonUserSessionForwarder:
    def __init__(self, client, bot_username: str, *, loop=None):
        if not bot_username:
            raise DeliveryFailure("bot_username_missing", "V2 原生转发缺少 Bot username")
        self._client = client
        self._bot_username = bot_username
        self._loop = loop

    def forward_batch(self, request: NativeForwardRequest) -> FirstHopForwardResult:
        coroutine = self._forward(request)
        if self._loop is None:
            return asyncio.run(coroutine)
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    async def _forward(self, request: NativeForwardRequest) -> FirstHopForwardResult:
        source_peer = _source_peer(request)
        try:
            bot_peer = await self._client.get_input_entity(f"@{self._bot_username}")
            await self._client.send_message(bot_peer, marker_text(request))
            forwarded = await self._client.forward_messages(
                bot_peer,
                list(request.source_message_ids),
                from_peer=source_peer,
            )
        except (DeliveryFailure, DeliveryUncertain):
            raise
        except Exception as exc:
            raise _forward_error(exc) from exc
        return FirstHopForwardResult(_forwarded_count(forwarded))


def _source_peer(request: NativeForwardRequest):
    if request.source_peer.access_hash is None:
        raise DeliveryFailure("native_forward_peer_invalid", "原私聊 peer 缺少 access hash")
    from telethon.tl.types import InputPeerUser

    return InputPeerUser(request.source_peer.id, request.source_peer.access_hash)


def _forwarded_count(messages) -> int:
    if not isinstance(messages, (list, tuple)):
        messages = (messages,)
    if not messages or any(getattr(message, "id", None) is None for message in messages):
        raise DeliveryUncertain("bridge_transport_unknown", "第一跳未返回可确认的转发消息")
    return len(messages)


def _forward_error(error: Exception) -> DeliveryFailure | DeliveryUncertain:
    from telethon.errors import (
        ChatForwardsRestrictedError,
        ChatWriteForbiddenError,
        FloodWaitError,
        MessageIdInvalidError,
        PeerIdInvalidError,
        RPCError,
    )

    if isinstance(error, (ConnectionError, OSError, TimeoutError)):
        return DeliveryUncertain("bridge_transport_unknown", f"第一跳结果未知: {error}")
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
    return DeliveryFailure("native_forward_first_hop_failed", str(error))
