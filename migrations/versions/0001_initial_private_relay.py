"""initial private relay schema

Revision ID: 0001_initial_private_relay
Revises:
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_private_relay"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_accounts()
    _create_session_and_auth_tables()
    _create_relay_tables()
    _create_outgoing_and_failover_tables()


def downgrade() -> None:
    for table_name in (
        "session_failover_events",
        "outgoing_replies",
        "reply_mappings",
        "bot_push_messages",
        "relay_messages",
        "auth_challenges",
        "tg_session_slots",
        "bound_tg_accounts",
        "system_users",
    ):
        op.drop_table(table_name)


def _create_accounts() -> None:
    op.create_table(
        "bound_tg_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system_user_id", sa.Integer(), sa.ForeignKey("system_users.id"), nullable=False),
        sa.Column("phone_number", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def _create_session_and_auth_tables() -> None:
    op.create_table(
        "tg_session_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bound_tg_account_id", sa.Integer(), sa.ForeignKey("bound_tg_accounts.id"), nullable=False),
        sa.Column("developer_slot", sa.String(length=32), nullable=False),
        sa.Column("encrypted_session", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("bound_tg_account_id", "developer_slot"),
    )
    op.create_table(
        "auth_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bound_tg_account_id", sa.Integer(), sa.ForeignKey("bound_tg_accounts.id"), nullable=False),
        sa.Column("phone_number", sa.String(length=64), nullable=False),
        sa.Column("developer_slot", sa.String(length=32), nullable=False),
        sa.Column("phone_code_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
    )


def _create_relay_tables() -> None:
    op.create_table(
        "relay_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bound_tg_account_id", sa.Integer(), sa.ForeignKey("bound_tg_accounts.id"), nullable=False),
        sa.Column("peer_id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("media_kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("media_group_id", sa.String(length=255), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("bound_tg_account_id", "source_message_id"),
    )
    op.create_table(
        "bot_push_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("relay_message_id", sa.Integer(), sa.ForeignKey("relay_messages.id"), nullable=False),
        sa.Column("system_user_id", sa.Integer(), sa.ForeignKey("system_users.id"), nullable=False),
        sa.Column("bot_message_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "reply_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_message_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("system_user_id", sa.Integer(), sa.ForeignKey("system_users.id"), nullable=False),
        sa.Column("relay_message_id", sa.Integer(), sa.ForeignKey("relay_messages.id"), nullable=False),
        sa.Column("bound_tg_account_id", sa.Integer(), nullable=False),
        sa.Column("peer_id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("media_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
    )


def _create_outgoing_and_failover_tables() -> None:
    op.create_table(
        "outgoing_replies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_reply_message_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("system_user_id", sa.Integer(), sa.ForeignKey("system_users.id"), nullable=False),
        sa.Column("relay_message_id", sa.Integer(), nullable=False),
        sa.Column("sent_message_id", sa.Integer(), nullable=False),
        sa.Column("developer_slot", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "session_failover_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bound_tg_account_id", sa.Integer(), nullable=False),
        sa.Column("from_slot", sa.String(length=32), nullable=False),
        sa.Column("to_slot", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
