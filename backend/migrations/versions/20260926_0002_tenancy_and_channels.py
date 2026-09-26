"""tenancy + channels: tenants, staff, channel accounts, contacts, conversations,
messages, webhook ingest queue, outbound queue, cross-tenant worker functions

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "app_user"
CHANNELS = "('messenger','instagram','whatsapp','tiktok')"

# نمط ثابت في كل الجداول:
#   UNIQUE (tenant_id, id)  +  مفاتيح أجنبية مركّبة (tenant_id, x_id)
#   => قاعدة البيانات نفسها تمنع ربط صف من وكالة بصف من وكالة أخرى
#      حتى لو أخطأ كود التطبيق.


def upgrade() -> None:
    # ------------------------------------------------------------ tenants
    op.execute("""
    CREATE TABLE tenants (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug             text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9-]{3,50}$'),
        name             text NOT NULL,
        business_type    text NOT NULL DEFAULT 'travel_hajj_umrah'
                         CHECK (business_type IN ('travel_hajj_umrah','retail','services','other')),
        status           text NOT NULL DEFAULT 'trial'
                         CHECK (status IN ('trial','active','suspended','cancelled')),
        timezone         text NOT NULL DEFAULT 'Africa/Tripoli',
        default_currency char(3) NOT NULL DEFAULT 'LYD',
        settings         jsonb NOT NULL DEFAULT '{}'::jsonb
                         CHECK (jsonb_typeof(settings) = 'object'),  -- مواعيد العمل، نبرة البوت...
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now()
    );
    """)
    # في جدول tenants المفتاح هو id نفسه
    op.execute("SELECT app_enable_tenant_rls('tenants', 'id')")
    # إنشاء/حذف وكالة عملية إدارية (app_owner) وليست من صلاحيات التطبيق
    op.execute(f"REVOKE INSERT, DELETE ON tenants FROM {RUNTIME_ROLE}")

    # ------------------------------------------------------------ staff_users
    op.execute("""
    CREATE TABLE staff_users (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        full_name          text NOT NULL,
        email              text,
        whatsapp_phone     text CHECK (whatsapp_phone ~ '^\\+[1-9][0-9]{7,14}$'),
        role               text NOT NULL DEFAULT 'sales'
                           CHECK (role IN ('owner','manager','sales','agent')),
        notify_on_new_lead boolean NOT NULL DEFAULT true,
        is_active          boolean NOT NULL DEFAULT true,
        created_at         timestamptz NOT NULL DEFAULT now(),
        updated_at         timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id)
    );
    CREATE UNIQUE INDEX staff_users_tenant_email_uq
        ON staff_users (tenant_id, lower(email)) WHERE email IS NOT NULL;
    """)
    op.execute("SELECT app_enable_tenant_rls('staff_users')")

    # ------------------------------------------------------------ channel_accounts
    op.execute(f"""
    CREATE TABLE channel_accounts (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        channel          text NOT NULL CHECK (channel IN {CHANNELS}),
        -- مفتاح التوجيه: page_id (Messenger) / ig_account_id (Instagram) / phone_number_id (WhatsApp)
        external_id      text NOT NULL,
        waba_id          text,                      -- WhatsApp Business Account ID
        display_name     text,
        access_token_enc bytea,                     -- مشفّر في طبقة التطبيق، لا يُخزّن نصاً صريحاً
        token_expires_at timestamptz,
        is_test          boolean NOT NULL DEFAULT false,  -- رقم الاختبار أثناء التطوير
        status           text NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','paused','disconnected')),
        config           jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now(),
        UNIQUE (channel, external_id),
        UNIQUE (tenant_id, id)
    );
    """)
    op.execute("SELECT app_enable_tenant_rls('channel_accounts')")

    # ------------------------------------------------------------ contacts
    op.execute(f"""
    CREATE TABLE contacts (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        channel          text NOT NULL CHECK (channel IN {CHANNELS}),
        external_user_id text NOT NULL,             -- PSID / IGSID / wa_id
        display_name     text,
        phone_e164       text CHECK (phone_e164 ~ '^\\+[1-9][0-9]{{7,14}}$'),
        profile          jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        first_seen_at    timestamptz NOT NULL DEFAULT now(),
        last_seen_at     timestamptz NOT NULL DEFAULT now(),
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, channel, external_user_id),
        UNIQUE (tenant_id, id)
    );
    CREATE INDEX contacts_tenant_phone_idx ON contacts (tenant_id, phone_e164)
        WHERE phone_e164 IS NOT NULL;
    """)
    op.execute("SELECT app_enable_tenant_rls('contacts')")

    # ------------------------------------------------------------ conversations
    op.execute("""
    CREATE TABLE conversations (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        contact_id         uuid NOT NULL,
        channel_account_id uuid NOT NULL,
        mode               text NOT NULL DEFAULT 'bot' CHECK (mode IN ('bot','human','closed')),
        bot_paused_until   timestamptz,             -- Human Handoff (Echo detection)
        last_inbound_at    timestamptz,             -- لحساب نافذة الـ 24 ساعة
        last_outbound_at   timestamptz,
        -- Debounce: كل رسالة واردة تدفع reply_due_at = now() + 4s
        reply_due_at       timestamptz,
        reply_lease_until  timestamptz,             -- حجز مؤقت للـ worker (يسقط تلقائياً عند التعطل)
        summary            text,                    -- ملخص للمحادثات الطويلة
        created_at         timestamptz NOT NULL DEFAULT now(),
        updated_at         timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, contact_id)
            REFERENCES contacts (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, channel_account_id)
            REFERENCES channel_accounts (tenant_id, id) ON DELETE CASCADE
    );
    -- محادثة مفتوحة واحدة فقط لكل زبون على كل حساب
    CREATE UNIQUE INDEX conversations_one_open_uq
        ON conversations (tenant_id, contact_id, channel_account_id)
        WHERE mode <> 'closed';
    CREATE INDEX conversations_reply_due_idx
        ON conversations (reply_due_at) WHERE reply_due_at IS NOT NULL;
    CREATE INDEX conversations_tenant_recent_idx
        ON conversations (tenant_id, last_inbound_at DESC);
    """)
    op.execute("SELECT app_enable_tenant_rls('conversations')")

    # ------------------------------------------------------------ messages
    op.execute(f"""
    CREATE TABLE messages (
        id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        conversation_id     uuid NOT NULL,
        channel             text NOT NULL CHECK (channel IN {CHANNELS}),
        direction           text NOT NULL CHECK (direction IN ('inbound','outbound')),
        sender_type         text NOT NULL CHECK (sender_type IN ('customer','bot','staff','system')),
        external_message_id text,                   -- mid / wamid (NULL للصادر قبل الإرسال)
        msg_type            text NOT NULL DEFAULT 'text'
                            CHECK (msg_type IN ('text','image','audio','video','document','location',
                                                'interactive','template','reaction','other')),
        text_content        text,
        payload             jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        platform_ts         timestamptz,
        created_at          timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id),
        -- Idempotency: Meta تعيد إرسال نفس الحدث => INSERT ... ON CONFLICT DO NOTHING
        UNIQUE (tenant_id, channel, external_message_id),
        FOREIGN KEY (tenant_id, conversation_id)
            REFERENCES conversations (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX messages_conversation_time_idx
        ON messages (tenant_id, conversation_id, created_at);
    """)
    op.execute("SELECT app_enable_tenant_rls('messages')")

    # ------------------------------------------------------------ webhook_events
    # بدون tenant_id وبدون RLS: عند الاستلام لا نعرف الوكالة بعد (نعرفها في الـ worker).
    op.execute("""
    CREATE TABLE webhook_events (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        provider        text NOT NULL DEFAULT 'meta' CHECK (provider IN ('meta','tiktok')),
        object_type     text,                       -- page / instagram / whatsapp_business_account
        payload         jsonb NOT NULL,
        status          text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','processing','done','failed','ignored')),
        attempts        int  NOT NULL DEFAULT 0,
        next_attempt_at timestamptz NOT NULL DEFAULT now(),
        locked_until    timestamptz,
        last_error      text,
        received_at     timestamptz NOT NULL DEFAULT now(),
        processed_at    timestamptz
    );
    CREATE INDEX webhook_events_pending_idx
        ON webhook_events (next_attempt_at) WHERE status IN ('pending','processing');
    CREATE INDEX webhook_events_received_idx ON webhook_events (received_at);
    """)

    # ------------------------------------------------------------ outbound_messages
    op.execute("""
    CREATE TABLE outbound_messages (
        id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        channel_account_id  uuid NOT NULL,          -- الحساب المرسِل (صفحة / IG / رقم واتساب)
        recipient           text NOT NULL,          -- PSID / IGSID / wa_id / رقم الموظف
        purpose             text NOT NULL DEFAULT 'reply'
                            CHECK (purpose IN ('reply','staff_notification','followup')),
        conversation_id     uuid,                   -- NULL لإشعارات الموظفين
        message_id          uuid,                   -- الرسالة المنطقية في جدول messages
        lead_id             uuid,                   -- لإشعار "Lead جديد" (FK يُضاف في 0004)
        kind                text NOT NULL CHECK (kind IN ('text','template','media','sender_action')),
        body                jsonb NOT NULL,
        status              text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','sending','sent','failed','cancelled')),
        attempts            int NOT NULL DEFAULT 0,
        next_attempt_at     timestamptz NOT NULL DEFAULT now(),
        locked_until        timestamptz,
        last_error          text,
        external_message_id text,
        created_at          timestamptz NOT NULL DEFAULT now(),
        sent_at             timestamptz,
        UNIQUE (tenant_id, id),
        CHECK (purpose <> 'reply' OR conversation_id IS NOT NULL),
        FOREIGN KEY (tenant_id, channel_account_id)
            REFERENCES channel_accounts (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, conversation_id)
            REFERENCES conversations (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, message_id)
            REFERENCES messages (tenant_id, id) ON DELETE SET NULL (message_id)
    );
    CREATE INDEX outbound_pending_idx
        ON outbound_messages (next_attempt_at) WHERE status IN ('pending','sending');
    """)
    op.execute("SELECT app_enable_tenant_rls('outbound_messages')")

    for t in ("tenants", "staff_users", "channel_accounts", "contacts", "conversations"):
        op.execute(f"""
        CREATE TRIGGER {t}_set_updated_at BEFORE UPDATE ON {t}
            FOR EACH ROW EXECUTE FUNCTION app_set_updated_at();
        """)

    # ==================================================================
    # دوال الـ Worker العابرة للوكالات (SECURITY DEFINER)
    # ------------------------------------------------------------------
    # المشكلة: الـ worker يحتاج أن يعرف "أي وكالة؟" قبل أن يضبط app.tenant_id،
    # ويحتاج أن يرى "المهام المستحقة" عبر كل الوكالات.
    # الحل: دوال ضيقة جداً تعيد (id, tenant_id) فقط — لا محتوى — ثم يفتح الـ worker
    # transaction مقيّدة بالوكالة لمعالجة كل مهمة. لا يوجد دور يتجاوز RLS في التشغيل.
    # ==================================================================
    op.execute(f"""
    CREATE FUNCTION resolve_channel_account(p_channel text, p_external_id text)
    RETURNS TABLE (channel_account_id uuid, tenant_id uuid, status text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT ca.id, ca.tenant_id, ca.status
        FROM channel_accounts ca
        JOIN tenants t ON t.id = ca.tenant_id
        WHERE ca.channel = p_channel
          AND ca.external_id = p_external_id
          AND t.status IN ('trial','active')
    $$;

    CREATE FUNCTION claim_due_conversations(p_batch int DEFAULT 20, p_lease interval DEFAULT '2 minutes')
    RETURNS TABLE (conversation_id uuid, tenant_id uuid, reply_due_at timestamptz)
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        WITH due AS (
            SELECT c.id
            FROM conversations c
            WHERE c.reply_due_at <= now()
              AND c.mode = 'bot'
              AND (c.bot_paused_until IS NULL OR c.bot_paused_until < now())
              AND (c.reply_lease_until IS NULL OR c.reply_lease_until < now())
            ORDER BY c.reply_due_at
            LIMIT p_batch
            FOR UPDATE SKIP LOCKED
        )
        UPDATE conversations c
           SET reply_lease_until = now() + p_lease
          FROM due
         WHERE c.id = due.id
        RETURNING c.id, c.tenant_id, c.reply_due_at
    $$;

    CREATE FUNCTION claim_pending_outbound(p_batch int DEFAULT 50, p_lease interval DEFAULT '1 minute')
    RETURNS TABLE (outbound_id uuid, tenant_id uuid)
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        WITH due AS (
            SELECT o.id
            FROM outbound_messages o
            WHERE o.status IN ('pending','sending')
              AND o.next_attempt_at <= now()
              AND (o.locked_until IS NULL OR o.locked_until < now())
            ORDER BY o.next_attempt_at
            LIMIT p_batch
            FOR UPDATE SKIP LOCKED
        )
        UPDATE outbound_messages o
           SET status = 'sending', locked_until = now() + p_lease, attempts = o.attempts + 1
          FROM due
         WHERE o.id = due.id
        RETURNING o.id, o.tenant_id
    $$;

    REVOKE ALL ON FUNCTION resolve_channel_account(text, text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION claim_due_conversations(int, interval) FROM PUBLIC;
    REVOKE ALL ON FUNCTION claim_pending_outbound(int, interval) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION resolve_channel_account(text, text) TO {RUNTIME_ROLE};
    GRANT EXECUTE ON FUNCTION claim_due_conversations(int, interval) TO {RUNTIME_ROLE};
    GRANT EXECUTE ON FUNCTION claim_pending_outbound(int, interval) TO {RUNTIME_ROLE};
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS claim_pending_outbound(int, interval);
    DROP FUNCTION IF EXISTS claim_due_conversations(int, interval);
    DROP FUNCTION IF EXISTS resolve_channel_account(text, text);
    DROP TABLE IF EXISTS outbound_messages;
    DROP TABLE IF EXISTS webhook_events;
    DROP TABLE IF EXISTS messages;
    DROP TABLE IF EXISTS conversations;
    DROP TABLE IF EXISTS contacts;
    DROP TABLE IF EXISTS channel_accounts;
    DROP TABLE IF EXISTS staff_users;
    DROP TABLE IF EXISTS tenants;
    """)
