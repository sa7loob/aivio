"""messages.handled_at: which inbound messages the bot has already answered

لماذا ليس last_outbound_at؟ الاعتماد على الوقت يُفقد رسالة وصلت في transaction متزامنة
بدأت قبل الرد وانتهت بعده (created_at أقدم من وقت الرد لكنها لم تكن مرئية).
علامة لكل رسالة لا تعاني من هذه المشكلة.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE messages ADD COLUMN handled_at timestamptz;
    CREATE INDEX messages_unhandled_inbound_idx
        ON messages (tenant_id, conversation_id, created_at)
        WHERE direction = 'inbound' AND handled_at IS NULL;
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX IF EXISTS messages_unhandled_inbound_idx;
    ALTER TABLE messages DROP COLUMN IF EXISTS handled_at;
    """)
