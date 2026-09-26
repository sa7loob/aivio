"""foundation: extensions, tenant context, Arabic normalizer, RLS helper, default grants

Revision ID: 0001
Revises:
Create Date: 2026-09-26
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

RUNTIME_ROLE = "app_user"


def upgrade() -> None:
    # الإضافات مثبّتة مسبقاً بواسطة superuser في init script؛ هنا للتأكيد فقط.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # فشل واضح إذا لم يُنشأ دور التشغيل
    op.execute(f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            RAISE EXCEPTION 'Role {RUNTIME_ROLE} is missing. Run docker/postgres/init first.';
        END IF;
    END $$;
    """)

    # ------------------------------------------------------------------
    # 1) سياق الـ Tenant الحالي
    #    - يُضبط في كل transaction عبر: SELECT set_config('app.tenant_id', '<uuid>', true)
    #    - NULLIF ضروري: بعد انتهاء transaction فيها SET LOCAL تعود القيمة '' وليس NULL
    #    - إذا لم يُضبط => NULL => السياسات لا تُظهر أي صف (Fail-closed)
    # ------------------------------------------------------------------
    op.execute("""
    CREATE FUNCTION app_current_tenant_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE AS $$
        SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
    $$;
    """)

    # ------------------------------------------------------------------
    # 2) توحيد النص العربي للبحث (IMMUTABLE لاستخدامه في أعمدة مولّدة وفهارس)
    #    - حذف التشكيل والتطويل
    #    - أ إ آ ٱ -> ا ، ة -> ه ، ى -> ي ، ؤ -> و ، ئ -> ي
    #    - الأرقام العربية -> لاتينية ، lower للحروف اللاتينية ، توحيد المسافات
    #    مثال: "عُمْرَة رمضان ١٤٤٨" == "عمره رمضان 1448"
    # ------------------------------------------------------------------
    op.execute(r"""
    CREATE FUNCTION app_normalize_ar(t text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
        SELECT btrim(regexp_replace(lower(
                 translate(
                   regexp_replace(t, E'[ً-ٰٟـ]', '', 'g'),
                   'أإآٱةىؤئ٠١٢٣٤٥٦٧٨٩',
                   'ااااهيوي0123456789'
                 )
               ), E'\\s+', ' ', 'g'))
    $$;
    """)

    # ------------------------------------------------------------------
    # 3) updated_at تلقائي
    # ------------------------------------------------------------------
    op.execute("""
    CREATE FUNCTION app_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        NEW.updated_at := now();
        RETURN NEW;
    END $$;
    """)

    # ------------------------------------------------------------------
    # 4) مُفعّل RLS موحّد لكل جدول فيه tenant_id
    #    استدعاء واحد لكل جدول جديد => لا يمكن نسيان WITH CHECK أو كتابة سياسة مختلفة.
    # ------------------------------------------------------------------
    op.execute("""
    CREATE FUNCTION app_enable_tenant_rls(tbl regclass, key_column text DEFAULT 'tenant_id')
    RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %s '
            'USING (%I = app_current_tenant_id()) '
            'WITH CHECK (%I = app_current_tenant_id())',
            tbl, key_column, key_column);
    END $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION app_enable_tenant_rls(regclass, text) FROM PUBLIC")

    # ------------------------------------------------------------------
    # 5) صلاحيات افتراضية: كل جدول/sequence ينشئه app_owner لاحقاً
    #    يصبح متاحاً لـ app_user (والحماية الفعلية تأتي من RLS)
    # ------------------------------------------------------------------
    op.execute(f"""
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RUNTIME_ROLE};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO {RUNTIME_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {RUNTIME_ROLE};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE USAGE, SELECT ON SEQUENCES FROM {RUNTIME_ROLE};
    """)
    op.execute("DROP FUNCTION IF EXISTS app_enable_tenant_rls(regclass, text)")
    op.execute("DROP FUNCTION IF EXISTS app_set_updated_at()")
    op.execute("DROP FUNCTION IF EXISTS app_normalize_ar(text)")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant_id()")
    # الإضافات تُترك: أنشأها superuser وقد تستخدمها قواعد أخرى
