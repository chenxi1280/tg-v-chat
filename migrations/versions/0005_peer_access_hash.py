"""store peer access hash for replies

Revision ID: 0005_peer_access_hash
Revises: 0004_auth_pending_session
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_peer_access_hash"
down_revision = "0004_auth_pending_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("relay_messages", sa.Column("peer_access_hash", sa.BigInteger(), nullable=True))
    op.add_column("reply_mappings", sa.Column("peer_access_hash", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("reply_mappings", "peer_access_hash")
    op.drop_column("relay_messages", "peer_access_hash")
