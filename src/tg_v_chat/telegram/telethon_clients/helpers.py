from __future__ import annotations

import asyncio

from tg_v_chat.domain import TelegramPeer


def _run_async(coro):
    return asyncio.run(coro)


def _input_peer(peer: TelegramPeer):
    if peer.access_hash is None:
        return peer.id
    from telethon.tl.types import InputPeerUser

    return InputPeerUser(peer.id, peer.access_hash)
