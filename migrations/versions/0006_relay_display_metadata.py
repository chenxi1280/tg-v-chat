"""store relay display metadata

Revision ID: 0006_relay_display_metadata
Revises: 0005_peer_access_hash
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_relay_display_metadata"
down_revision = "0005_peer_access_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("relay_messages", sa.Column("sender_name", sa.Text(), nullable=True))
    op.add_column("relay_messages", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("relay_messages", "sent_at")
    op.drop_column("relay_messages", "sender_name")
