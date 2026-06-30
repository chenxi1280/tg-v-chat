"""store bound account display identity

Revision ID: 0008_account_display_identity
Revises: 0007_scope_bot_message_ids
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_account_display_identity"
down_revision = "0007_scope_bot_message_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bound_tg_accounts", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("bound_tg_accounts", sa.Column("username", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("bound_tg_accounts", "username")
    op.drop_column("bound_tg_accounts", "display_name")
