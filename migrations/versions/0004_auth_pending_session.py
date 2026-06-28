"""store pending login session for phone code auth

Revision ID: 0004_auth_pending_session
Revises: 0003_use_bigint_for_telegram_ids
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_auth_pending_session"
down_revision = "0003_use_bigint_for_telegram_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_challenges", sa.Column("pending_session", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_challenges", "pending_session")
