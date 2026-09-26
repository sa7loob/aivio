"""leads: booking interest captured by the agent + follow-up event log

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OPEN_STATUSES = "('new','contacted','qualified')"


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE leads (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        contact_id       uuid NOT NULL,
        conversation_id  uuid,
        package_id       uuid,
        departure_id     uuid,

        -- بيانات الحجز المبدئي (بدون جوازات سفر في الـ MVP)
        full_name        text NOT NULL,
        phone_e164       text NOT NULL CHECK (phone_e164 ~ '^\\+[1-9][0-9]{{7,14}}$'),
        city             text,
        adults           smallint NOT NULL DEFAULT 1 CHECK (adults >= 0),
        children         smallint NOT NULL DEFAULT 0 CHECK (children >= 0),
        infants          smallint NOT NULL DEFAULT 0 CHECK (infants >= 0),
        room_type_pref   text CHECK (room_type_pref IN ('quad','triple','double','single','shared','na')),
        preferred_period text,                       -- "العشر الأواخر"، "شهر 12"
        budget_note      text,
        notes            text,

        status           text NOT NULL DEFAULT 'new'
                         CHECK (status IN ('new','contacted','qualified','booked','lost','spam')),
        lost_reason      text,
        assigned_to      uuid,
        source_channel   text NOT NULL CHECK (source_channel IN ('messenger','instagram','whatsapp','tiktok','manual')),
        collected_by     text NOT NULL DEFAULT 'bot' CHECK (collected_by IN ('bot','staff')),

        notified_at      timestamptz,                -- متى أُشعر موظف المبيعات
        first_contact_at timestamptz,                -- متى تواصل الموظف (لقياس سرعة الاستجابة)
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now(),

        CHECK (adults + children + infants > 0),
        CHECK (departure_id IS NULL OR package_id IS NOT NULL),
        CHECK (status <> 'lost' OR lost_reason IS NOT NULL),
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, contact_id)      REFERENCES contacts (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id)
            ON DELETE SET NULL (conversation_id),
        FOREIGN KEY (tenant_id, package_id)      REFERENCES packages (tenant_id, id)
            ON DELETE SET NULL (package_id),
        FOREIGN KEY (tenant_id, package_id, departure_id)
            REFERENCES package_departures (tenant_id, package_id, id)
            ON DELETE SET NULL (departure_id),
        FOREIGN KEY (tenant_id, assigned_to)     REFERENCES staff_users (tenant_id, id)
            ON DELETE SET NULL (assigned_to)
    );

    -- Lead مفتوح واحد فقط لكل زبون على نفس البرنامج:
    -- create_lead يعمل UPSERT بدل تكرار الفرص إذا عاد الزبون وكرر نفس الطلب
    CREATE UNIQUE INDEX leads_one_open_per_contact_package_uq
        ON leads (tenant_id, contact_id, package_id) NULLS NOT DISTINCT
        WHERE status IN {OPEN_STATUSES};

    CREATE INDEX leads_pipeline_idx   ON leads (tenant_id, status, created_at DESC);
    CREATE INDEX leads_phone_idx      ON leads (tenant_id, phone_e164);
    CREATE INDEX leads_assignee_idx   ON leads (tenant_id, assigned_to) WHERE status IN {OPEN_STATUSES};

    -- إشعار الموظف يُرسل كصف في outbound_messages (purpose='staff_notification')
    -- فيستفيد من نفس آلية إعادة المحاولة
    ALTER TABLE outbound_messages
        ADD CONSTRAINT outbound_messages_lead_fk FOREIGN KEY (tenant_id, lead_id)
        REFERENCES leads (tenant_id, id) ON DELETE SET NULL (lead_id);

    CREATE TRIGGER leads_set_updated_at BEFORE UPDATE ON leads
        FOR EACH ROW EXECUTE FUNCTION app_set_updated_at();
    """)
    op.execute("SELECT app_enable_tenant_rls('leads')")

    op.execute("""
    CREATE TABLE lead_events (
        id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        lead_id        uuid NOT NULL,
        event_type     text NOT NULL CHECK (event_type IN (
                           'created','updated','status_changed','assigned','note',
                           'notification_sent','notification_failed')),
        data           jsonb NOT NULL DEFAULT '{}'::jsonb,
        actor_type     text NOT NULL CHECK (actor_type IN ('bot','staff','system')),
        actor_staff_id uuid,
        created_at     timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, lead_id)        REFERENCES leads (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, actor_staff_id) REFERENCES staff_users (tenant_id, id)
            ON DELETE SET NULL (actor_staff_id)
    );
    CREATE INDEX lead_events_lead_idx ON lead_events (tenant_id, lead_id, created_at);
    """)
    op.execute("SELECT app_enable_tenant_rls('lead_events')")


def downgrade() -> None:
    op.execute("ALTER TABLE outbound_messages DROP CONSTRAINT IF EXISTS outbound_messages_lead_fk")
    op.execute("DROP TABLE IF EXISTS lead_events")
    op.execute("DROP TABLE IF EXISTS leads")
