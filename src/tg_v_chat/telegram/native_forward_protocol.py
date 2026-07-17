from __future__ import annotations

from dataclasses import dataclass

from tg_v_chat.domain import NativeForwardRequest


MARKER_PREFIX = "tgvc-forward-v2"
MAX_BRIDGE_ITEMS = 100


@dataclass(frozen=True)
class ForwardBridgeMarker:
    token: str
    expected_count: int


def marker_text(request: NativeForwardRequest) -> str:
    return f"{MARKER_PREFIX}:{request.marker_token}:{request.expected_count}"


def parse_marker(text: str | None) -> ForwardBridgeMarker | None:
    if text is None:
        return None
    parts = text.split(":")
    if len(parts) != 3 or parts[0] != MARKER_PREFIX or not parts[1]:
        return None
    try:
        expected_count = int(parts[2])
    except ValueError:
        return None
    if expected_count < 1 or expected_count > MAX_BRIDGE_ITEMS:
        return None
    return ForwardBridgeMarker(parts[1], expected_count)


def is_marker_candidate(text: str | None) -> bool:
    return bool(text and text.startswith(MARKER_PREFIX))
