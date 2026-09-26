"""Phase 7a: AI job queue (voice transcription, brochure extraction, knowledge embeddings),
catalog imports (brochure -> draft packages), staff answers as knowledge.

- ai_jobs: طابور واحد لمهام الذكاء الاصطناعي بنفس نمط outbound_messages
  (SKIP LOCKED + lease + retries). claim_ai_jobs() تعيد معرّفات فقط وتتخطى الوكالات الموقوفة.
- catalog_imports: ملف البروشور حتى تتم معالجته (API والـ worker حاويتان بدون مجلد مشترك)،
  ثم يُمسح file_data. البرامج المستخرجة تُحفظ draft مع packages.source_import_id.
- knowledge_chunks: created_by (من علّم البوت) + UNIQUE (tenant_id, id) المطلوب لكل جدول وكالة.
- تحديث نص رسالة (تفريغ صوتي) يرسل حدث 'message' للوحة.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-26
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "app_user"


def upgrade() -> None:
    # ------------------------------------------------------------------ catalog_imports
    op.execute("""
    CREATE TABLE catalog_imports (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        created_by       uuid REFERENCES users(id) ON DELETE SET NULL,
        filename         text CHECK (char_length(filename) <= 200),
        mime_type        text NOT NULL CHECK (mime_type IN ('image/jpeg','image/png','image/webp','application/pdf')),
        size_bytes       int  NOT NULL CHECK (size_bytes > 0),
        sha256           text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        file_data        bytea,                       -- يُمسح بعد انتهاء المعالجة
        status           text NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','processing','done','failed')),
        error            text,
        warnings         jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(warnings) = 'array'),
        packages_created int  NOT NULL DEFAULT 0 CHECK (packages_created >= 0),
        created_at       timestamptz NOT NULL DEFAULT now(),
        finished_at      timestamptz,
        UNIQUE (tenant_id, id),
        CHECK (status IN ('done','failed') OR file_data IS NOT NULL)
    );
    CREATE INDEX catalog_imports_tenant_recent_idx ON catalog_imports (tenant_id, created_at DESC, id DESC);
    CREATE INDEX catalog_imports_tenant_sha_idx ON catalog_imports (tenant_id, sha256);
    """)
    op.execute("SELECT app_enable_tenant_rls('catalog_imports')")

    op.execute("""
    ALTER TABLE packages ADD COLUMN source_import_id uuid;
    ALTER TABLE packages ADD CONSTRAINT packages_source_import_fk
        FOREIGN KEY (tenant_id, source_import_id) REFERENCES catalog_imports (tenant_id, id)
        ON DELETE SET NULL (source_import_id);
    CREATE INDEX packages_source_import_idx ON packages (tenant_id, source_import_id)
        WHERE source_import_id IS NOT NULL;
    """)

    # ------------------------------------------------------------------ knowledge_chunks
    op.execute("""
    ALTER TABLE knowledge_chunks ADD CONSTRAINT knowledge_chunks_tenant_id_id_key UNIQUE (tenant_id, id);
    ALTER TABLE knowledge_chunks ADD COLUMN created_by uuid REFERENCES users(id) ON DELETE SET NULL;
    CREATE INDEX knowledge_chunks_source_ref_idx ON knowledge_chunks (tenant_id, source_ref)
        WHERE source_ref IS NOT NULL;
    """)

    # ------------------------------------------------------------------ ai_jobs
    op.execute("""
    CREATE TABLE ai_jobs (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        kind               text NOT NULL CHECK (kind IN ('transcribe','catalog_extract','embed_knowledge')),
        message_id         uuid,
        catalog_import_id  uuid,
        knowledge_chunk_id uuid,
        status             text NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','processing','done','failed')),
        attempts           int  NOT NULL DEFAULT 0,
        next_attempt_at    timestamptz NOT NULL DEFAULT now(),
        locked_until       timestamptz,
        last_error         text,
        -- لقياس التكلفة والزمن لكل وكالة
        model              text,
        input_tokens       int NOT NULL DEFAULT 0,
        output_tokens      int NOT NULL DEFAULT 0,
        duration_ms        int,
        created_at         timestamptz NOT NULL DEFAULT now(),
        finished_at        timestamptz,
        UNIQUE (tenant_id, id),
        -- مرجع واحد فقط، ويطابق نوع المهمة
        CHECK ((kind = 'transcribe') = (message_id IS NOT NULL)
               AND (kind = 'catalog_extract') = (catalog_import_id IS NOT NULL)
               AND (kind = 'embed_knowledge') = (knowledge_chunk_id IS NOT NULL)),
        FOREIGN KEY (tenant_id, message_id) REFERENCES messages (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, catalog_import_id) REFERENCES catalog_imports (tenant_id, id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, knowledge_chunk_id) REFERENCES knowledge_chunks (tenant_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX ai_jobs_transcribe_uq ON ai_jobs (tenant_id, message_id) WHERE kind = 'transcribe';
    CREATE UNIQUE INDEX ai_jobs_catalog_uq ON ai_jobs (tenant_id, catalog_import_id) WHERE kind = 'catalog_extract';
    CREATE UNIQUE INDEX ai_jobs_embed_active_uq ON ai_jobs (tenant_id, knowledge_chunk_id)
        WHERE kind = 'embed_knowledge' AND status IN ('pending','processing');
    CREATE INDEX ai_jobs_due_idx ON ai_jobs (next_attempt_at) WHERE status IN ('pending','processing');
    CREATE INDEX ai_jobs_tenant_recent_idx ON ai_jobs (tenant_id, created_at);
    """)
    op.execute("SELECT app_enable_tenant_rls('ai_jobs')")

    # الـ worker يرى المهام المستحقة عبر كل الوكالات (معرّفات فقط)، ثم يعالج كل مهمة داخل سياق وكالتها
    op.execute(f"""
    CREATE FUNCTION claim_ai_jobs(p_kinds text[], p_batch int DEFAULT 5, p_lease interval DEFAULT '3 minutes')
    RETURNS TABLE (job_id uuid, tenant_id uuid, kind text)
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        WITH due AS (
            SELECT j.id FROM ai_jobs j
             WHERE j.status IN ('pending','processing')
               AND j.kind = ANY (p_kinds)
               AND j.next_attempt_at <= now()
               AND (j.locked_until IS NULL OR j.locked_until < now())
               -- وكالة موقوفة: لا إنفاق على الذكاء الاصطناعي (نفس قاعدة claim_due_conversations)
               AND NOT EXISTS (SELECT 1 FROM subscriptions s
                                WHERE s.tenant_id = j.tenant_id AND s.status IN ('suspended','cancelled'))
             ORDER BY j.next_attempt_at
             LIMIT p_batch
               FOR UPDATE SKIP LOCKED
        )
        UPDATE ai_jobs j
           SET status = 'processing', locked_until = now() + p_lease, attempts = j.attempts + 1
          FROM due
         WHERE j.id = due.id
        RETURNING j.id, j.tenant_id, j.kind
    $$;
    REVOKE ALL ON FUNCTION claim_ai_jobs(text[], int, interval) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION claim_ai_jobs(text[], int, interval) TO {RUNTIME_ROLE};
    """)

    # ------------------------------------------------------------------ realtime: نص رسالة تغيّر (تفريغ صوتي)
    op.execute("""
    CREATE TRIGGER messages_notify_text_update AFTER UPDATE OF text_content ON messages
        FOR EACH ROW WHEN (OLD.text_content IS DISTINCT FROM NEW.text_content)
        EXECUTE FUNCTION app_notify_tenant_event();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS messages_notify_text_update ON messages;
    DROP FUNCTION IF EXISTS claim_ai_jobs(text[], int, interval);
    DROP TABLE IF EXISTS ai_jobs;

    DROP INDEX IF EXISTS knowledge_chunks_source_ref_idx;
    ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS created_by;
    ALTER TABLE knowledge_chunks DROP CONSTRAINT IF EXISTS knowledge_chunks_tenant_id_id_key;

    DROP INDEX IF EXISTS packages_source_import_idx;
    ALTER TABLE packages DROP CONSTRAINT IF EXISTS packages_source_import_fk;
    ALTER TABLE packages DROP COLUMN IF EXISTS source_import_id;
    DROP TABLE IF EXISTS catalog_imports;
    """)
