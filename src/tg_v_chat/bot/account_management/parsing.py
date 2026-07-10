from __future__ import annotations

from dataclasses import dataclass

from tg_v_chat.domain import DeveloperSlot


@dataclass(frozen=True)
class SlotAction:
    account_id: int
    slot: DeveloperSlot
    action: str


def _parse_id(data: str) -> int:
    try:
        return int(data.rsplit(":", 1)[1])
    except ValueError as exc:
        raise ValueError(f"无效账号操作: {data}") from exc


def _require_challenge(challenge_id: int | None) -> int:
    if challenge_id is None:
        raise RuntimeError("当前绑定状态缺少认证挑战，请重新开始绑定。")
    return challenge_id


def parse_slot_action(data: str) -> SlotAction:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] not in {"account.slot.bind", "account.slot.reauth"}:
        raise ValueError(f"无效槽位授权操作: {data}")
    try:
        return SlotAction(int(parts[1]), DeveloperSlot(parts[2]), parts[0].rsplit(".", 1)[1])
    except (ValueError, TypeError) as exc:
        raise ValueError(f"无效槽位授权操作: {data}") from exc
