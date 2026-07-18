"""remove native forward sender-side message ids

Revision ID: 0012_remove_sender_side_ids
Revises: 0011_native_forward_item_scope
Create Date: 2026-07-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_remove_sender_side_ids"
down_revision = "0011_native_forward_item_scope"
branch_labels = None
depends_on = None


EXPECTED_BRIDGE_MESSAGE_UNIQUE = "uq_native_forward_item_expected_bridge_message"


def upgrade() -> None:
    op.drop_constraint(EXPECTED_BRIDGE_MESSAGE_UNIQUE, "native_forward_items", type_="unique")
    op.drop_column("native_forward_items", "expected_bridge_message_id")


def downgrade() -> None:
    op.add_column(
        "native_forward_items",
        sa.Column("expected_bridge_message_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        EXPECTED_BRIDGE_MESSAGE_UNIQUE,
        "native_forward_items",
        ["bridge_sender_telegram_user_id", "expected_bridge_message_id"],
    )
