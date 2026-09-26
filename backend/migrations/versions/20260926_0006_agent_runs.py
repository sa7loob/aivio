"""agent_runs: one row per agent invocation (cost, latency, tool trace, failures)

يُستخدم لـ: مراقبة التكلفة لكل وكالة، تتبّع لماذا قال البوت كذا، وبناء مجموعة تقييم
من محادثات حقيقية لاحقاً.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE agent_runs (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        conversation_id uuid NOT NULL,
        reply_message_id uuid,
        status          text NOT NULL CHECK (status IN ('ok','fallback','error')),
        model           text NOT NULL,
        iterations      smallint NOT NULL DEFAULT 0,
        input_tokens    int NOT NULL DEFAULT 0,
        output_tokens   int NOT NULL DEFAULT 0,
        latency_ms      int NOT NULL DEFAULT 0,
        tool_calls      jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{name, args, ok, ms}]
        handed_off      boolean NOT NULL DEFAULT false,
        lead_id         uuid,
        error           text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, conversation_id)
            REFERENCES conversations (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, reply_message_id)
            REFERENCES messages (tenant_id, id) ON DELETE SET NULL (reply_message_id),
        FOREIGN KEY (tenant_id, lead_id)
            REFERENCES leads (tenant_id, id) ON DELETE SET NULL (lead_id)
    );
    CREATE INDEX agent_runs_conversation_idx ON agent_runs (tenant_id, conversation_id, created_at);
    CREATE INDEX agent_runs_tenant_day_idx   ON agent_runs (tenant_id, created_at);
    """)
    op.execute("SELECT app_enable_tenant_rls('agent_runs')")

    # تجريد خفيف للسوابق العربية: "والاسترجاع" و"الاسترجاع" و"بالاسترجاع" => "استرجاع".
    # (الإعداد 'simple' لا يفهم السوابق؛ بدون هذا تفشل مطابقة كثير من الأسئلة في البحث النصي)
    op.execute(r"""
    CREATE FUNCTION app_ar_light_stem(t text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
        SELECT regexp_replace(t, E'(^|\\s)(وال|بال|فال|كال|لل|ال)(\\S{3,})', E'\\1\\3', 'g')
    $$;
    """)

    # إعادة بناء content_tsv بالتجريد (الجدول ما زال فارغاً في هذه المرحلة)
    op.execute("""
    DROP INDEX IF EXISTS knowledge_chunks_tsv_gin;
    ALTER TABLE knowledge_chunks DROP COLUMN content_tsv;
    ALTER TABLE knowledge_chunks ADD COLUMN content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', app_ar_light_stem(app_normalize_ar(coalesce(title, '') || ' ' || content)))
    ) STORED;
    CREATE INDEX knowledge_chunks_tsv_gin ON knowledge_chunks USING gin (content_tsv);
    """)

    # tsquery بصيغة OR من نص حر، بنفس التوحيد والتجريد المستخدمين في الفهرس
    op.execute(r"""
    CREATE FUNCTION app_or_tsquery(p_text text) RETURNS tsquery
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
        SELECT to_tsquery('simple', coalesce(nullif(array_to_string(ARRAY(
                   SELECT quote_literal(w) || ':*'
                   FROM regexp_split_to_table(
                       app_ar_light_stem(regexp_replace(app_normalize_ar(coalesce(p_text, '')),
                                         E'[''"\\\\&|!():*<>,.;؟?،]', ' ', 'g')),
                       E'\\s+') AS w
                   WHERE char_length(w) >= 2), ' | '), ''), 'a & !a'))
    $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_or_tsquery(text)")
    op.execute("""
    DROP INDEX IF EXISTS knowledge_chunks_tsv_gin;
    ALTER TABLE knowledge_chunks DROP COLUMN content_tsv;
    ALTER TABLE knowledge_chunks ADD COLUMN content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', app_normalize_ar(coalesce(title, '') || ' ' || content))
    ) STORED;
    CREATE INDEX knowledge_chunks_tsv_gin ON knowledge_chunks USING gin (content_tsv);
    """)
    op.execute("DROP FUNCTION IF EXISTS app_ar_light_stem(text)")
    op.execute("DROP TABLE IF EXISTS agent_runs")
