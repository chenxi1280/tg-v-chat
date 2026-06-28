from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SystemUserModel(Base):
    __tablename__ = "system_users"

    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(Integer, unique=True, nullable=False)
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
    failure_reason = Column(Text, nullable=True)
    account = relationship("BoundTgAccountModel")


class AuthChallengeModel(Base):
    __tablename__ = "auth_challenges"

    id = Column(Integer, primary_key=True)
    bound_tg_account_id = Column(Integer, ForeignKey("bound_tg_accounts.id"), nullable=False)
    phone_number = Column(String(64), nullable=False)
    developer_slot = Column(String(32), nullable=False)
    phone_code_hash = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False, default="code_required")


class RelayMessageModel(Base):
    __tablename__ = "relay_messages"
    __table_args__ = (UniqueConstraint("bound_tg_account_id", "source_message_id"),)

    id = Column(Integer, primary_key=True)
    bound_tg_account_id = Column(Integer, ForeignKey("bound_tg_accounts.id"), nullable=False)
    peer_id = Column(Integer, nullable=False)
    source_message_id = Column(Integer, nullable=False)
    media_kind = Column(String(32), nullable=False)
    payload = Column(Text, nullable=False)
    media_group_id = Column(String(255), nullable=True)
    sequence = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="received")


class BotPushMessageModel(Base):
    __tablename__ = "bot_push_messages"

    id = Column(Integer, primary_key=True)
    relay_message_id = Column(Integer, ForeignKey("relay_messages.id"), nullable=False)
    system_user_id = Column(Integer, ForeignKey("system_users.id"), nullable=False)
    bot_message_id = Column(Integer, nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="pushed")


class ReplyMappingModel(Base):
    __tablename__ = "reply_mappings"

    id = Column(Integer, primary_key=True)
    bot_message_id = Column(Integer, nullable=False, unique=True)
    system_user_id = Column(Integer, ForeignKey("system_users.id"), nullable=False)
    relay_message_id = Column(Integer, ForeignKey("relay_messages.id"), nullable=False)
    bound_tg_account_id = Column(Integer, nullable=False)
    peer_id = Column(Integer, nullable=False)
    source_message_id = Column(Integer, nullable=False)
    media_kind = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="active")


class OutgoingReplyModel(Base):
    __tablename__ = "outgoing_replies"

    id = Column(Integer, primary_key=True)
    bot_reply_message_id = Column(Integer, unique=True, nullable=False)
    system_user_id = Column(Integer, ForeignKey("system_users.id"), nullable=False)
    relay_message_id = Column(Integer, nullable=False)
    sent_message_id = Column(Integer, nullable=False)
    developer_slot = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="sent")


class SessionFailoverEventModel(Base):
    __tablename__ = "session_failover_events"

    id = Column(Integer, primary_key=True)
    bound_tg_account_id = Column(Integer, nullable=False)
    from_slot = Column(String(32), nullable=False)
    to_slot = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
