"""scope native forward bridge item ids

Revision ID: 0011_native_forward_item_scope
Revises: 0010_native_forward_bridge
Create Date: 2026-07-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_native_forward_item_scope"
down_revision = "0010_native_forward_bridge"
branch_labels = None
depends_on = None


BRIDGE_MESSAGE_UNIQUE = "uq_native_forward_item_bridge_message"
EXPECTED_BRIDGE_MESSAGE_UNIQUE = "uq_native_forward_item_expected_bridge_message"


def upgrade() -> None:
    op.add_column(
        "native_forward_items",
        sa.Column("bridge_sender_telegram_user_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "native_forward_items",
        sa.Column("expected_bridge_message_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE native_forward_items AS item
        SET bridge_sender_telegram_user_id = batch.bridge_sender_telegram_user_id
        FROM native_forward_batches AS batch
        WHERE item.batch_id = batch.id
        """
    )
    op.execute(
        """
        UPDATE native_forward_items
        SET expected_bridge_message_id = bridge_message_id
        WHERE bridge_message_id IS NOT NULL
        """
    )
    op.alter_column("native_forward_items", "bridge_sender_telegram_user_id", nullable=False)
    op.drop_constraint(BRIDGE_MESSAGE_UNIQUE, "native_forward_items", type_="unique")
    op.create_unique_constraint(
        BRIDGE_MESSAGE_UNIQUE,
        "native_forward_items",
        ["bridge_sender_telegram_user_id", "bridge_message_id"],
    )
    op.create_unique_constraint(
        EXPECTED_BRIDGE_MESSAGE_UNIQUE,
        "native_forward_items",
        ["bridge_sender_telegram_user_id", "expected_bridge_message_id"],
    )
    op.execute("UPDATE bound_tg_accounts SET telegram_user_id = NULL WHERE status = 'deleted'")


def downgrade() -> None:
    op.drop_constraint(EXPECTED_BRIDGE_MESSAGE_UNIQUE, "native_forward_items", type_="unique")
    op.drop_constraint(BRIDGE_MESSAGE_UNIQUE, "native_forward_items", type_="unique")
    op.create_unique_constraint(BRIDGE_MESSAGE_UNIQUE, "native_forward_items", ["bridge_message_id"])
    op.drop_column("native_forward_items", "expected_bridge_message_id")
    op.drop_column("native_forward_items", "bridge_sender_telegram_user_id")
