"""add bot conversation states

Revision ID: 0002_bot_conversation_states
Revises: 0001_initial_private_relay
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_bot_conversation_states"
down_revision = "0001_initial_private_relay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_conversation_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system_user_id", sa.Integer(), sa.ForeignKey("system_users.id"), nullable=False, unique=True),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("auth_challenge_id", sa.Integer(), sa.ForeignKey("auth_challenges.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bot_conversation_states")
