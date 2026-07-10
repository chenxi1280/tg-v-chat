"""harden relay runtime persistence

Revision ID: 0009_relay_runtime_hardening
Revises: 0008_account_display_identity
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_relay_runtime_hardening"
down_revision = "0008_account_display_identity"
branch_labels = None
depends_on = None


DISPATCH_CHECK = "status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')"


def upgrade() -> None:
    _preflight_orphan_relationships()
    _extend_reply_mappings()
    _extend_bot_pushes()
    _extend_outgoing_replies()
    _extend_session_runtime()
    _extend_failover_events()
    _create_media_tables()


def downgrade() -> None:
    _preflight_downgrade_results()
    op.drop_table("relay_media_artifacts")
    op.drop_table("relay_media_groups")
    _downgrade_failover_events()
    _downgrade_session_runtime()
    _downgrade_outgoing_replies()
    _downgrade_bot_pushes()
    _downgrade_reply_mappings()


def _preflight_orphan_relationships() -> None:
    op.execute(
        sa.text(
            """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM reply_mappings m LEFT JOIN bound_tg_accounts a ON a.id = m.bound_tg_account_id WHERE a.id IS NULL) THEN
        RAISE EXCEPTION '0009 preflight: orphan reply_mappings.bound_tg_account_id';
    END IF;
    IF EXISTS (SELECT 1 FROM outgoing_replies o LEFT JOIN relay_messages r ON r.id = o.relay_message_id WHERE r.id IS NULL) THEN
        RAISE EXCEPTION '0009 preflight: orphan outgoing_replies.relay_message_id';
    END IF;
    IF EXISTS (SELECT 1 FROM session_failover_events f LEFT JOIN bound_tg_accounts a ON a.id = f.bound_tg_account_id WHERE a.id IS NULL) THEN
        RAISE EXCEPTION '0009 preflight: orphan session_failover_events.bound_tg_account_id';
    END IF;
END $$;
"""
        )
    )


def _extend_reply_mappings() -> None:
    op.add_column(
        "reply_mappings",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.add_column("reply_mappings", sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_reply_mappings_bound_account",
        "reply_mappings",
        "bound_tg_accounts",
        ["bound_tg_account_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_reply_mapping_status",
        "reply_mappings",
        "status IN ('active', 'expired')",
    )


def _extend_bot_pushes() -> None:
    op.alter_column("bot_push_messages", "bot_message_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("bot_push_messages", sa.Column("dispatch_key", sa.String(length=255), nullable=True))
    op.add_column("bot_push_messages", sa.Column("failure_code", sa.String(length=64), nullable=True))
    op.add_column("bot_push_messages", sa.Column("failure_reason", sa.Text(), nullable=True))
    _add_dispatch_timestamps("bot_push_messages")
    op.execute("UPDATE bot_push_messages SET dispatch_key = 'push:' || id, status = 'sent'")
    op.alter_column("bot_push_messages", "dispatch_key", existing_type=sa.String(length=255), nullable=False)
    op.create_unique_constraint("uq_bot_push_relay", "bot_push_messages", ["relay_message_id"])
    op.create_unique_constraint("uq_bot_push_dispatch_key", "bot_push_messages", ["dispatch_key"])
    op.create_check_constraint("ck_bot_push_dispatch_status", "bot_push_messages", DISPATCH_CHECK)


def _extend_outgoing_replies() -> None:
    op.alter_column("outgoing_replies", "sent_message_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("outgoing_replies", "developer_slot", existing_type=sa.String(length=32), nullable=True)
    op.add_column("outgoing_replies", sa.Column("dispatch_key", sa.String(length=255), nullable=True))
    op.add_column("outgoing_replies", sa.Column("failure_code", sa.String(length=64), nullable=True))
    op.add_column("outgoing_replies", sa.Column("failure_reason", sa.Text(), nullable=True))
    _add_dispatch_timestamps("outgoing_replies")
    op.execute("UPDATE outgoing_replies SET dispatch_key = 'outgoing:' || system_user_id || ':' || bot_reply_message_id")
    op.alter_column("outgoing_replies", "dispatch_key", existing_type=sa.String(length=255), nullable=False)
    op.create_unique_constraint("uq_outgoing_reply_dispatch_key", "outgoing_replies", ["dispatch_key"])
    op.create_check_constraint("ck_outgoing_reply_dispatch_status", "outgoing_replies", DISPATCH_CHECK)
    op.create_foreign_key(
        "fk_outgoing_replies_relay_message",
        "outgoing_replies",
        "relay_messages",
        ["relay_message_id"],
        ["id"],
    )


def _add_dispatch_timestamps(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.add_column(
        table_name,
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )


def _extend_session_runtime() -> None:
    op.add_column("tg_session_slots", sa.Column("failure_code", sa.String(length=64), nullable=True))
    op.add_column("tg_session_slots", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tg_session_slots", sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "tg_session_slots",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.add_column("tg_session_slots", sa.Column("revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column(
        "auth_challenges",
        sa.Column("purpose", sa.String(length=32), server_default="initial_bind", nullable=False),
    )


def _extend_failover_events() -> None:
    op.alter_column("session_failover_events", "to_slot", existing_type=sa.String(length=32), nullable=True)
    op.create_foreign_key(
        "fk_session_failover_events_bound_account",
        "session_failover_events",
        "bound_tg_accounts",
        ["bound_tg_account_id"],
        ["id"],
    )


def _create_media_tables() -> None:
    op.create_table(
        "relay_media_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bound_tg_account_id", sa.Integer(), sa.ForeignKey("bound_tg_accounts.id"), nullable=False),
        sa.Column("media_group_id", sa.String(length=255), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("dispatch_key", sa.String(length=255), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bound_tg_account_id", "media_group_id", name="uq_media_group_account_group"),
        sa.UniqueConstraint("dispatch_key", name="uq_media_group_dispatch_key"),
        sa.CheckConstraint(DISPATCH_CHECK, name="ck_media_group_dispatch_status"),
    )
    _create_media_artifacts()


def _create_media_artifacts() -> None:
    op.create_table(
        "relay_media_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("relay_message_id", sa.Integer(), sa.ForeignKey("relay_messages.id"), nullable=True),
        sa.Column("outgoing_reply_id", sa.Integer(), sa.ForeignKey("outgoing_replies.id"), nullable=True),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_kind", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('staging', 'ready', 'sent', 'failed', 'released')",
            name="ck_relay_media_artifact_status",
        ),
    )


def _downgrade_failover_events() -> None:
    op.drop_constraint("fk_session_failover_events_bound_account", "session_failover_events", type_="foreignkey")
    op.alter_column("session_failover_events", "to_slot", existing_type=sa.String(length=32), nullable=False)


def _downgrade_session_runtime() -> None:
    op.drop_column("auth_challenges", "purpose")
    for column_name in ("revision", "updated_at", "last_healthy_at", "last_checked_at", "failure_code"):
        op.drop_column("tg_session_slots", column_name)


def _downgrade_outgoing_replies() -> None:
    op.drop_constraint("fk_outgoing_replies_relay_message", "outgoing_replies", type_="foreignkey")
    op.drop_constraint("ck_outgoing_reply_dispatch_status", "outgoing_replies", type_="check")
    op.drop_constraint("uq_outgoing_reply_dispatch_key", "outgoing_replies", type_="unique")
    for column_name in ("updated_at", "created_at", "failure_reason", "failure_code", "dispatch_key"):
        op.drop_column("outgoing_replies", column_name)
    op.alter_column("outgoing_replies", "developer_slot", existing_type=sa.String(length=32), nullable=False)
    op.alter_column("outgoing_replies", "sent_message_id", existing_type=sa.Integer(), nullable=False)


def _downgrade_bot_pushes() -> None:
    op.drop_constraint("ck_bot_push_dispatch_status", "bot_push_messages", type_="check")
    op.drop_constraint("uq_bot_push_dispatch_key", "bot_push_messages", type_="unique")
    op.drop_constraint("uq_bot_push_relay", "bot_push_messages", type_="unique")
    op.execute("UPDATE bot_push_messages SET status = 'pushed' WHERE status = 'sent'")
    for column_name in ("updated_at", "created_at", "failure_reason", "failure_code", "dispatch_key"):
        op.drop_column("bot_push_messages", column_name)
    op.alter_column("bot_push_messages", "bot_message_id", existing_type=sa.Integer(), nullable=False)


def _downgrade_reply_mappings() -> None:
    op.drop_constraint("ck_reply_mapping_status", "reply_mappings", type_="check")
    op.drop_constraint("fk_reply_mappings_bound_account", "reply_mappings", type_="foreignkey")
    op.drop_column("reply_mappings", "invalidated_at")
    op.drop_column("reply_mappings", "created_at")


def _preflight_downgrade_results() -> None:
    op.execute(
        sa.text(
            """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM reply_mappings WHERE status = 'expired') THEN
        RAISE EXCEPTION '0009 downgrade: expired ReplyMapping would be reactivated';
    END IF;
    IF EXISTS (SELECT 1 FROM bot_push_messages WHERE bot_message_id IS NULL) THEN
        RAISE EXCEPTION '0009 downgrade: bot_message_id contains NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM outgoing_replies WHERE sent_message_id IS NULL OR developer_slot IS NULL) THEN
        RAISE EXCEPTION '0009 downgrade: outgoing result contains NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM session_failover_events WHERE to_slot IS NULL) THEN
        RAISE EXCEPTION '0009 downgrade: exhausted failover has nullable to_slot';
    END IF;
END $$;
"""
        )
    )
