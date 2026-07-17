"""add native forward bridge persistence

Revision ID: 0010_native_forward_bridge
Revises: 0009_relay_runtime_hardening
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_native_forward_bridge"
down_revision = "0009_relay_runtime_hardening"
branch_labels = None
depends_on = None


BATCH_STATUS_CHECK = (
    "status IN ('collecting', 'sealed', 'bridge_sending', 'awaiting_bot', "
    "'final_sending', 'sent', 'failed', 'uncertain')"
)
ITEM_STATUS_CHECK = "status IN ('pending', 'bridged', 'sent', 'failed', 'uncertain')"
LEGACY_RELAY_MESSAGE_UNIQUE = "relay_messages_bound_tg_account_id_source_message_id_key"
RELAY_MESSAGE_PEER_UNIQUE = "uq_relay_message_account_peer_source"


def upgrade() -> None:
    op.drop_constraint(LEGACY_RELAY_MESSAGE_UNIQUE, "relay_messages", type_="unique")
    op.create_unique_constraint(
        RELAY_MESSAGE_PEER_UNIQUE,
        "relay_messages",
        ["bound_tg_account_id", "peer_id", "source_message_id"],
    )
    op.add_column("bound_tg_accounts", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        "uq_bound_tg_account_telegram_user_id",
        "bound_tg_accounts",
        ["telegram_user_id"],
    )
    _create_batches()
    _create_items()
    _create_quarantine()


def downgrade() -> None:
    op.drop_table("native_forward_bridge_quarantines")
    op.drop_table("native_forward_items")
    op.drop_table("native_forward_batches")
    op.drop_constraint("uq_bound_tg_account_telegram_user_id", "bound_tg_accounts", type_="unique")
    op.drop_column("bound_tg_accounts", "telegram_user_id")
    op.drop_constraint(RELAY_MESSAGE_PEER_UNIQUE, "relay_messages", type_="unique")
    op.create_unique_constraint(
        LEGACY_RELAY_MESSAGE_UNIQUE,
        "relay_messages",
        ["bound_tg_account_id", "source_message_id"],
    )


def _create_batches() -> None:
    op.create_table(
        "native_forward_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system_user_id", sa.Integer(), sa.ForeignKey("system_users.id"), nullable=False),
        sa.Column("bound_tg_account_id", sa.Integer(), sa.ForeignKey("bound_tg_accounts.id"), nullable=False),
        sa.Column("bridge_sender_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_peer_id", sa.BigInteger(), nullable=False),
        sa.Column("source_peer_access_hash", sa.BigInteger(), nullable=True),
        sa.Column("marker_token", sa.String(length=128), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="collecting"),
        sa.Column("collect_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bridge_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_hop_marker_message_id", sa.Integer(), nullable=True),
        sa.Column("header_bot_message_id", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("marker_token", name="uq_native_forward_batch_marker_token"),
        sa.CheckConstraint("expected_count >= 0 AND expected_count <= 100", name="ck_native_forward_batch_count"),
        sa.CheckConstraint(BATCH_STATUS_CHECK, name="ck_native_forward_batch_status"),
    )


def _create_items() -> None:
    op.create_table(
        "native_forward_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("native_forward_batches.id"), nullable=False),
        sa.Column("relay_message_id", sa.Integer(), sa.ForeignKey("relay_messages.id"), nullable=False),
        sa.Column("batch_sequence", sa.Integer(), nullable=False),
        sa.Column("bridge_message_id", sa.Integer(), nullable=True),
        sa.Column("bot_push_message_id", sa.Integer(), sa.ForeignKey("bot_push_messages.id"), nullable=True),
        sa.Column("final_bot_message_id", sa.Integer(), nullable=True),
        sa.Column("identity_visibility", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_id", "batch_sequence", name="uq_native_forward_item_batch_sequence"),
        sa.UniqueConstraint("relay_message_id", name="uq_native_forward_item_relay"),
        sa.UniqueConstraint("bridge_message_id", name="uq_native_forward_item_bridge_message"),
        sa.UniqueConstraint("bot_push_message_id", name="uq_native_forward_item_push"),
        sa.CheckConstraint(ITEM_STATUS_CHECK, name="ck_native_forward_item_status"),
        sa.CheckConstraint(
            "identity_visibility IS NULL OR identity_visibility IN ('linked', 'name_only')",
            name="ck_native_forward_item_identity_visibility",
        ),
    )


def _create_quarantine() -> None:
    op.create_table(
        "native_forward_bridge_quarantines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sender_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_message_id", sa.Integer(), nullable=False),
        sa.Column("marker_token", sa.String(length=128), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
