"""scope bot message ids by system user

Revision ID: 0007_scope_bot_message_ids
Revises: 0006_relay_display_metadata
Create Date: 2026-06-29
"""

from alembic import op


revision = "0007_scope_bot_message_ids"
down_revision = "0006_relay_display_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("bot_push_messages_bot_message_id_key", "bot_push_messages", type_="unique")
    op.drop_constraint("reply_mappings_bot_message_id_key", "reply_mappings", type_="unique")
    op.drop_constraint("outgoing_replies_bot_reply_message_id_key", "outgoing_replies", type_="unique")
    op.create_unique_constraint(
        "uq_bot_push_user_message",
        "bot_push_messages",
        ["system_user_id", "bot_message_id"],
    )
    op.create_unique_constraint(
        "uq_reply_mapping_user_message",
        "reply_mappings",
        ["system_user_id", "bot_message_id"],
    )
    op.create_unique_constraint(
        "uq_outgoing_reply_user_message",
        "outgoing_replies",
        ["system_user_id", "bot_reply_message_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_outgoing_reply_user_message", "outgoing_replies", type_="unique")
    op.drop_constraint("uq_reply_mapping_user_message", "reply_mappings", type_="unique")
    op.drop_constraint("uq_bot_push_user_message", "bot_push_messages", type_="unique")
    op.create_unique_constraint(
        "outgoing_replies_bot_reply_message_id_key",
        "outgoing_replies",
        ["bot_reply_message_id"],
    )
    op.create_unique_constraint(
        "reply_mappings_bot_message_id_key",
        "reply_mappings",
        ["bot_message_id"],
    )
    op.create_unique_constraint(
        "bot_push_messages_bot_message_id_key",
        "bot_push_messages",
        ["bot_message_id"],
    )
