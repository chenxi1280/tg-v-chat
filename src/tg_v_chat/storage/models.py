from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SystemUserModel(Base):
    __tablename__ = "system_users"

    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class BotConversationStateModel(Base):
    __tablename__ = "bot_conversation_states"

    id = Column(Integer, primary_key=True)
    system_user_id = Column(Integer, ForeignKey("system_users.id"), unique=True, nullable=False)
    state = Column(String(64), nullable=False)
    auth_challenge_id = Column(Integer, ForeignKey("auth_challenges.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    user = relationship("SystemUserModel")


class BoundTgAccountModel(Base):
    __tablename__ = "bound_tg_accounts"

    id = Column(Integer, primary_key=True)
    system_user_id = Column(Integer, ForeignKey("system_users.id"), nullable=False)
    phone_number = Column(String(64), nullable=False)
    display_name = Column(Text, nullable=True)
    username = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="binding")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    user = relationship("SystemUserModel")


class TgSessionSlotModel(Base):
    __tablename__ = "tg_session_slots"
    __table_args__ = (UniqueConstraint("bound_tg_account_id", "developer_slot"),)

    id = Column(Integer, primary_key=True)
    bound_tg_account_id = Column(Integer, ForeignKey("bound_tg_accounts.id"), nullable=False)
    developer_slot = Column(String(32), nullable=False)
    encrypted_session = Column(Text, nullable=True)
    status = Column(String(32), nullable=False)
    failure_code = Column(String(64), nullable=True)
    failure_reason = Column(Text, nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    last_healthy_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    revision = Column(Integer, nullable=False, default=0)
    account = relationship("BoundTgAccountModel")


class AuthChallengeModel(Base):
    __tablename__ = "auth_challenges"

    id = Column(Integer, primary_key=True)
    bound_tg_account_id = Column(Integer, ForeignKey("bound_tg_accounts.id"), nullable=False)
    phone_number = Column(String(64), nullable=False)
    developer_slot = Column(String(32), nullable=False)
    phone_code_hash = Column(String(255), nullable=False)
    pending_session = Column(Text, nullable=True)
    purpose = Column(String(32), nullable=False, default="initial_bind")
    status = Column(String(32), nullable=False, default="code_required")


class RelayMessageModel(Base):
    __tablename__ = "relay_messages"
    __table_args__ = (UniqueConstraint("bound_tg_account_id", "source_message_id"),)

    id = Column(Integer, primary_key=True)
    bound_tg_account_id = Column(Integer, ForeignKey("bound_tg_accounts.id"), nullable=False)
    peer_id = Column(BigInteger, nullable=False)
    peer_access_hash = Column(BigInteger, nullable=True)
    sender_name = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    source_message_id = Column(Integer, nullable=False)
    media_kind = Column(String(32), nullable=False)
    payload = Column(Text, nullable=False)
    media_group_id = Column(String(255), nullable=True)
    sequence = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="received")


class BotPushMessageModel(Base):
    __tablename__ = "bot_push_messages"
    __table_args__ = (
        UniqueConstraint("system_user_id", "bot_message_id", name="uq_bot_push_user_message"),
        UniqueConstraint("relay_message_id", name="uq_bot_push_relay"),
        UniqueConstraint("dispatch_key", name="uq_bot_push_dispatch_key"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')",
            name="ck_bot_push_dispatch_status",
        ),
    )
    DISPATCH_STATUSES = ("pending", "sending", "sent", "failed", "uncertain")

    id = Column(Integer, primary_key=True)
    relay_message_id = Column(Integer, ForeignKey("relay_messages.id"), nullable=False)
    system_user_id = Column(Integer, ForeignKey("system_users.id"), nullable=False)
    bot_message_id = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    dispatch_key = Column(String(255), nullable=False)
    failure_code = Column(String(64), nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class ReplyMappingModel(Base):
    __tablename__ = "reply_mappings"
    __table_args__ = (
        UniqueConstraint("system_user_id", "bot_message_id", name="uq_reply_mapping_user_message"),
        CheckConstraint("status IN ('active', 'expired')", name="ck_reply_mapping_status"),
    )

    id = Column(Integer, primary_key=True)
    bot_message_id = Column(Integer, nullable=False)
    system_user_id = Column(Integer, ForeignKey("system_users.id"), nullable=False)
    relay_message_id = Column(Integer, ForeignKey("relay_messages.id"), nullable=False)
    bound_tg_account_id = Column(Integer, ForeignKey("bound_tg_accounts.id"), nullable=False)
    peer_id = Column(BigInteger, nullable=False)
    peer_access_hash = Column(BigInteger, nullable=True)
    source_message_id = Column(Integer, nullable=False)
    media_kind = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)


class OutgoingReplyModel(Base):
    __tablename__ = "outgoing_replies"
    __table_args__ = (
        UniqueConstraint("system_user_id", "bot_reply_message_id", name="uq_outgoing_reply_user_message"),
        UniqueConstraint("dispatch_key", name="uq_outgoing_reply_dispatch_key"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')",
            name="ck_outgoing_reply_dispatch_status",
        ),
    )
    DISPATCH_STATUSES = ("pending", "sending", "sent", "failed", "uncertain")

    id = Column(Integer, primary_key=True)
    bot_reply_message_id = Column(Integer, nullable=False)
    system_user_id = Column(Integer, ForeignKey("system_users.id"), nullable=False)
    relay_message_id = Column(Integer, ForeignKey("relay_messages.id"), nullable=False)
    sent_message_id = Column(Integer, nullable=True)
    developer_slot = Column(String(32), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    dispatch_key = Column(String(255), nullable=False)
    failure_code = Column(String(64), nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class SessionFailoverEventModel(Base):
    __tablename__ = "session_failover_events"

    id = Column(Integer, primary_key=True)
    bound_tg_account_id = Column(Integer, ForeignKey("bound_tg_accounts.id"), nullable=False)
    from_slot = Column(String(32), nullable=False)
    to_slot = Column(String(32), nullable=True)
    status = Column(String(32), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class RelayMediaArtifactModel(Base):
    __tablename__ = "relay_media_artifacts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('staging', 'ready', 'sent', 'failed', 'released')",
            name="ck_relay_media_artifact_status",
        ),
    )
    ARTIFACT_STATUSES = ("staging", "ready", "sent", "failed", "released")

    id = Column(Integer, primary_key=True)
    relay_message_id = Column(Integer, ForeignKey("relay_messages.id"), nullable=True)
    outgoing_reply_id = Column(Integer, ForeignKey("outgoing_replies.id"), nullable=True)
    direction = Column(String(32), nullable=False)
    storage_key = Column(String(255), nullable=False, unique=True)
    file_name = Column(Text, nullable=False)
    mime_type = Column(String(255), nullable=False)
    byte_size = Column(BigInteger, nullable=False)
    media_kind = Column(String(32), nullable=False)
    sequence = Column(Integer, nullable=False)
    metadata_json = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="staging")
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    released_at = Column(DateTime(timezone=True), nullable=True)


class RelayMediaGroupModel(Base):
    __tablename__ = "relay_media_groups"
    __table_args__ = (
        UniqueConstraint("bound_tg_account_id", "media_group_id", name="uq_media_group_account_group"),
        UniqueConstraint("dispatch_key", name="uq_media_group_dispatch_key"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')",
            name="ck_media_group_dispatch_status",
        ),
    )
    DISPATCH_STATUSES = ("pending", "sending", "sent", "failed", "uncertain")

    id = Column(Integer, primary_key=True)
    bound_tg_account_id = Column(Integer, ForeignKey("bound_tg_accounts.id"), nullable=False)
    media_group_id = Column(String(255), nullable=False)
    item_count = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    dispatch_key = Column(String(255), nullable=False)
    failure_code = Column(String(64), nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
