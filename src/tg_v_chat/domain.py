from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


MAX_BOUND_ACCOUNTS = 20


class DeveloperSlot(str, Enum):
    PRIMARY = "primary"
    STANDBY_1 = "standby_1"
    STANDBY_2 = "standby_2"


class MediaKind(str, Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    VIDEO_NOTE = "video_note"
    AUDIO = "audio"
    VOICE = "voice"
    STICKER = "sticker"


class NativeForwardBatchStatus(str, Enum):
    COLLECTING = "collecting"
    SEALED = "sealed"
    BRIDGE_SENDING = "bridge_sending"
    AWAITING_BOT = "awaiting_bot"
    FINAL_SENDING = "final_sending"
    SENT = "sent"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class NativeForwardItemStatus(str, Enum):
    PENDING = "pending"
    BRIDGED = "bridged"
    SENT = "sent"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    STANDBY = "standby"
    FAILED = "failed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class SessionFailure(RuntimeError):
    """Raised for session-layer failures that can trigger failover."""


@dataclass(frozen=True)
class DeliveryFailure(RuntimeError):
    code: str
    detail: str


@dataclass(frozen=True)
class DeliveryUncertain(RuntimeError):
    code: str
    detail: str


@dataclass(frozen=True)
class MediaArtifact:
    storage_key: str
    file_name: str
    mime_type: str
    byte_size: int
    media_kind: MediaKind
    sequence: int
    metadata_json: str | None = None


@dataclass(frozen=True)
class TelegramPeer:
    id: int
    access_hash: int | None = None


@dataclass(frozen=True)
class NativeForwardRequest:
    batch_id: int
    marker_token: str
    expected_count: int
    source_peer: TelegramPeer
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True)
class FirstHopForwardResult:
    forwarded_count: int


class UserSessionForwarder(Protocol):
    def forward_batch(self, request: NativeForwardRequest) -> FirstHopForwardResult:
        raise NotImplementedError


@dataclass(frozen=True)
class IncomingPrivateMessage:
    bound_tg_account_id: int
    peer_id: int
    source_message_id: int
    media_kind: MediaKind
    payload: str
    media_group_id: str | None
    sequence: int
    peer_access_hash: int | None = None
    sender_name: str | None = None
    sent_at: datetime | None = None
    recipient_account_name: str | None = None
    recipient_username: str | None = None
    artifacts: tuple[MediaArtifact, ...] = ()


@dataclass(frozen=True)
class IncomingPrivateBatch:
    messages: tuple[IncomingPrivateMessage, ...]


@dataclass(frozen=True)
class OutgoingReply:
    system_user_id: int
    bot_reply_message_id: int
    reply_to_message_id: int | None
    media_kind: MediaKind
    payload: str
    artifacts: tuple[MediaArtifact, ...] = ()


@dataclass(frozen=True)
class IncomingRelayResult:
    relay_message_id: int
    bot_message_id: int | None
    duplicate: bool


@dataclass(frozen=True)
class OutgoingRelayResult:
    relay_message_id: int
    sent_message_id: int
    developer_slot: DeveloperSlot
    duplicate: bool


@dataclass(frozen=True)
class SessionSlotRef:
    id: int
    bound_tg_account_id: int
    developer_slot: DeveloperSlot
    encrypted_session: str | None
