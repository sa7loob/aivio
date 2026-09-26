"""Live Inbox + human takeover + realtime notifications.

- conversations: ملخص للقائمة (آخر رسالة، غير المقروء) يُحدَّث بـ trigger عند كل رسالة
  => صحيح دائماً مهما كان مصدر الرسالة (ingest / echo / البوت / الموظف).
- استلام بشري صريح: takeover_by / takeover_at + mode='human'. رد الموظف من اللوحة
  = purpose 'staff_reply' (منفصل عن 'reply' الخاص بالبوت => الاستلام يلغي ردود البوت المعلّقة فقط).
- messages.client_msg_id: idempotency لإرسال الموظف (إعادة المحاولة من الواجهة لا تكرر الرسالة).
- NOTIFY tenant_events: معرّفات فقط (tenant_id + نوع + id)، والتطبيق يرشّح حسب الوكالة ثم
  تعيد الواجهة الجلب عبر الـ API المحمي بـ RLS => الإشعار نفسه لا يحمل أي بيانات زبائن.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-26
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE conversations
        ADD COLUMN assigned_user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN takeover_by          uuid REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN takeover_at          timestamptz,
        ADD COLUMN last_message_at      timestamptz,
        ADD COLUMN last_message_preview text,
        ADD COLUMN last_message_direction text CHECK (last_message_direction IN ('inbound','outbound')),
        ADD COLUMN unread_count         int NOT NULL DEFAULT 0 CHECK (unread_count >= 0);

    -- قائمة الـ Inbox: الأحدث أولاً داخل الوكالة (keyset pagination)
    CREATE INDEX conversations_inbox_idx
        ON conversations (tenant_id, last_message_at DESC NULLS LAST, id DESC);
    CREATE INDEX conversations_assigned_idx
        ON conversations (tenant_id, assigned_user_id) WHERE mode <> 'closed';

    ALTER TABLE messages ADD COLUMN client_msg_id uuid;
    CREATE UNIQUE INDEX messages_client_msg_uq ON messages (tenant_id, client_msg_id)
        WHERE client_msg_id IS NOT NULL;
    CREATE INDEX messages_conversation_page_idx ON messages (tenant_id, conversation_id, created_at DESC, id DESC);

    ALTER TABLE outbound_messages DROP CONSTRAINT outbound_messages_purpose_check;
    ALTER TABLE outbound_messages ADD CONSTRAINT outbound_messages_purpose_check
        CHECK (purpose IN ('reply','staff_reply','staff_notification','followup'));
    ALTER TABLE outbound_messages DROP CONSTRAINT outbound_messages_check;
    ALTER TABLE outbound_messages ADD CONSTRAINT outbound_messages_check
        CHECK (purpose NOT IN ('reply','staff_reply') OR conversation_id IS NOT NULL);
    CREATE INDEX outbound_messages_message_idx ON outbound_messages (tenant_id, message_id)
        WHERE message_id IS NOT NULL;

    -- رد البوت الذي انتهى بعد استلام الموظف للمحادثة يُسجَّل ولا يُرسل
    ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_status_check;
    ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_status_check
        CHECK (status IN ('ok','fallback','error','discarded'));

    CREATE INDEX leads_tenant_created_idx ON leads (tenant_id, created_at DESC, id DESC);
    """)

    # ------------------------------------------------------------------ summary trigger
    op.execute("""
    CREATE FUNCTION app_conversation_on_message() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        UPDATE conversations
           SET last_message_at = greatest(coalesce(last_message_at, '-infinity'), NEW.created_at),
               last_message_preview = left(coalesce(NULLIF(btrim(NEW.text_content), ''),
                                                    '[' || NEW.msg_type || ']'), 140),
               last_message_direction = NEW.direction,
               unread_count = unread_count + CASE WHEN NEW.direction = 'inbound' THEN 1 ELSE 0 END
         WHERE id = NEW.conversation_id;
        RETURN NULL;
    END $$;
    CREATE TRIGGER messages_update_conversation AFTER INSERT ON messages
        FOR EACH ROW EXECUTE FUNCTION app_conversation_on_message();

    -- تعبئة المحادثات الموجودة
    UPDATE conversations c SET
        last_message_at = m.created_at,
        last_message_preview = left(coalesce(NULLIF(btrim(m.text_content), ''), '[' || m.msg_type || ']'), 140),
        last_message_direction = m.direction
      FROM (SELECT DISTINCT ON (conversation_id) conversation_id, created_at, text_content, msg_type, direction
              FROM messages ORDER BY conversation_id, created_at DESC, id DESC) m
     WHERE m.conversation_id = c.id;
    """)

    # ------------------------------------------------------------------ realtime NOTIFY
    op.execute("""
    CREATE FUNCTION app_notify_tenant_event() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE v_type text; v_id uuid; v_conv uuid;
    BEGIN
        IF TG_TABLE_NAME = 'messages' THEN
            v_type := 'message'; v_id := NEW.id; v_conv := NEW.conversation_id;
        ELSIF TG_TABLE_NAME = 'conversations' THEN
            v_type := 'conversation'; v_id := NEW.id; v_conv := NEW.id;
        ELSE
            v_type := 'lead'; v_id := NEW.id; v_conv := NEW.conversation_id;
        END IF;
        PERFORM pg_notify('tenant_events', json_build_object(
            't', NEW.tenant_id, 'type', v_type, 'id', v_id, 'conversation_id', v_conv)::text);
        RETURN NULL;
    END $$;

    CREATE TRIGGER messages_notify AFTER INSERT ON messages
        FOR EACH ROW EXECUTE FUNCTION app_notify_tenant_event();
    -- تغيّرات تهم الواجهة فقط (وليس كل تحديث لحقول الـ worker مثل reply_lease_until)
    CREATE TRIGGER conversations_notify AFTER UPDATE ON conversations
        FOR EACH ROW WHEN (OLD.mode IS DISTINCT FROM NEW.mode
                           OR OLD.assigned_user_id IS DISTINCT FROM NEW.assigned_user_id
                           OR OLD.bot_paused_until IS DISTINCT FROM NEW.bot_paused_until
                           OR OLD.unread_count IS DISTINCT FROM NEW.unread_count)
        EXECUTE FUNCTION app_notify_tenant_event();
    CREATE TRIGGER leads_notify AFTER INSERT OR UPDATE ON leads
        FOR EACH ROW EXECUTE FUNCTION app_notify_tenant_event();
    """)

    # حالة الإرسال (sending/sent/failed) تظهر بجانب رسالة الموظف في الواجهة
    op.execute("""
    CREATE FUNCTION app_notify_outbound_status() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM pg_notify('tenant_events', json_build_object(
            't', NEW.tenant_id, 'type', 'message_status', 'id', NEW.message_id,
            'conversation_id', NEW.conversation_id)::text);
        RETURN NULL;
    END $$;
    CREATE TRIGGER outbound_status_notify AFTER UPDATE OF status ON outbound_messages
        FOR EACH ROW WHEN (NEW.message_id IS NOT NULL AND OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION app_notify_outbound_status();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS outbound_status_notify ON outbound_messages;
    DROP FUNCTION IF EXISTS app_notify_outbound_status();
    DROP TRIGGER IF EXISTS leads_notify ON leads;
    DROP TRIGGER IF EXISTS conversations_notify ON conversations;
    DROP TRIGGER IF EXISTS messages_notify ON messages;
    DROP FUNCTION IF EXISTS app_notify_tenant_event();
    DROP TRIGGER IF EXISTS messages_update_conversation ON messages;
    DROP FUNCTION IF EXISTS app_conversation_on_message();
    DROP INDEX IF EXISTS leads_tenant_created_idx;

    UPDATE agent_runs SET status = 'error' WHERE status = 'discarded';
    ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_status_check;
    ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_status_check CHECK (status IN ('ok','fallback','error'));

    DROP INDEX IF EXISTS outbound_messages_message_idx;
    UPDATE outbound_messages SET purpose = 'reply' WHERE purpose = 'staff_reply';
    ALTER TABLE outbound_messages DROP CONSTRAINT outbound_messages_check;
    ALTER TABLE outbound_messages ADD CONSTRAINT outbound_messages_check
        CHECK (purpose <> 'reply' OR conversation_id IS NOT NULL);
    ALTER TABLE outbound_messages DROP CONSTRAINT outbound_messages_purpose_check;
    ALTER TABLE outbound_messages ADD CONSTRAINT outbound_messages_purpose_check
        CHECK (purpose IN ('reply','staff_notification','followup'));

    DROP INDEX IF EXISTS messages_conversation_page_idx;
    DROP INDEX IF EXISTS messages_client_msg_uq;
    ALTER TABLE messages DROP COLUMN IF EXISTS client_msg_id;

    DROP INDEX IF EXISTS conversations_assigned_idx;
    DROP INDEX IF EXISTS conversations_inbox_idx;
    ALTER TABLE conversations
        DROP COLUMN IF EXISTS unread_count, DROP COLUMN IF EXISTS last_message_direction,
        DROP COLUMN IF EXISTS last_message_preview, DROP COLUMN IF EXISTS last_message_at,
        DROP COLUMN IF EXISTS takeover_at, DROP COLUMN IF EXISTS takeover_by,
        DROP COLUMN IF EXISTS assigned_user_id;
    """)
