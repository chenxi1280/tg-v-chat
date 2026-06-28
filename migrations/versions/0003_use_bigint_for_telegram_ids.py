"""use bigint for telegram identifiers

Revision ID: 0003_use_bigint_for_telegram_ids
Revises: 0002_bot_conversation_states
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_use_bigint_for_telegram_ids"
down_revision = "0002_bot_conversation_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("system_users", "telegram_user_id", type_=sa.BigInteger(), existing_nullable=False)
    op.alter_column("relay_messages", "peer_id", type_=sa.BigInteger(), existing_nullable=False)
    op.alter_column("reply_mappings", "peer_id", type_=sa.BigInteger(), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("reply_mappings", "peer_id", type_=sa.Integer(), existing_nullable=False)
    op.alter_column("relay_messages", "peer_id", type_=sa.Integer(), existing_nullable=False)
    op.alter_column("system_users", "telegram_user_id", type_=sa.Integer(), existing_nullable=False)
