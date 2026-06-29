from __future__ import annotations


def _parse_id(data: str) -> int:
    try:
        return int(data.rsplit(":", 1)[1])
    except ValueError as exc:
        raise ValueError(f"无效账号操作: {data}") from exc


def _require_challenge(challenge_id: int | None) -> int:
    if challenge_id is None:
        raise RuntimeError("当前绑定状态缺少认证挑战，请重新开始绑定。")
    return challenge_id
