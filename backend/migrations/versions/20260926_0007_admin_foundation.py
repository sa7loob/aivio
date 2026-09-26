"""admin foundation: platform admins, API tokens, audit log, and the app_admin DB role grants

دور app_admin (يُنشأ بواسطة superuser — راجع docker/postgres/init و docker/postgres/manual):
  - تستخدمه Admin API فقط (عملية منفصلة، غير معروضة للإنترنت).
  - BYPASSRLS: يرى كل الوكالات (عمل الدعم والمالية يتطلب ذلك).
  - لا يملك أي جدول ولا DDL. وكل تغيير على الأرصدة يمر عبر دوال محددة فقط (migration 0008).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-26
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ADMIN_ROLE = "app_admin"


def upgrade() -> None:
    op.execute(f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_ROLE}') THEN
            RAISE EXCEPTION 'Role {ADMIN_ROLE} is missing. Run docker/postgres/manual/create_app_admin_role.sql as superuser first.';
        END IF;
    END $$;
    """)

    op.execute("""
    CREATE TABLE admin_users (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email      text NOT NULL,
        full_name  text NOT NULL,
        role       text NOT NULL CHECK (role IN ('superadmin','support','finance')),
        is_active  boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX admin_users_email_uq ON admin_users (lower(email));

    -- توكن API لكل مشرف: يُخزَّن hash فقط، والنص الصريح يظهر مرة واحدة عند الإنشاء
    CREATE TABLE admin_tokens (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        admin_id     uuid NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
        token_hash   text NOT NULL UNIQUE,
        label        text,
        expires_at   timestamptz,
        revoked_at   timestamptz,
        last_used_at timestamptz,
        created_at   timestamptz NOT NULL DEFAULT now()
    );

    -- سجل تدقيق لكل عملية إدارية (append-only)
    CREATE TABLE admin_audit_log (
        id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        admin_id         uuid REFERENCES admin_users(id),
        action           text NOT NULL,
        target_tenant_id uuid REFERENCES tenants(id) ON DELETE SET NULL,
        target_type      text,
        target_id        text,
        details          jsonb NOT NULL DEFAULT '{}'::jsonb,
        ip               inet,
        created_at       timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX admin_audit_tenant_idx ON admin_audit_log (target_tenant_id, created_at DESC);
    CREATE INDEX admin_audit_time_idx   ON admin_audit_log (created_at DESC);
    """)

    # حالة القناة: needs_reauth عند سقوط التوكن (فحص دوري / خطأ 190)
    op.execute("""
    ALTER TABLE channel_accounts DROP CONSTRAINT channel_accounts_status_check;
    ALTER TABLE channel_accounts ADD CONSTRAINT channel_accounts_status_check
        CHECK (status IN ('active','paused','disconnected','needs_reauth'));
    ALTER TABLE channel_accounts ADD COLUMN last_checked_at timestamptz;
    ALTER TABLE channel_accounts ADD COLUMN last_error text;
    """)

    # دور التشغيل العام لا يرى جداول الإدارة إطلاقاً
    op.execute("REVOKE ALL ON admin_users, admin_tokens, admin_audit_log FROM app_user")

    # صلاحيات app_admin على كل الجداول الحالية + المستقبلية
    op.execute(f"""
    GRANT USAGE ON SCHEMA public TO {ADMIN_ROLE};
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ADMIN_ROLE};
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ADMIN_ROLE};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ADMIN_ROLE};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO {ADMIN_ROLE};
    -- سجل التدقيق لا يُعدَّل ولا يُحذف
    REVOKE UPDATE, DELETE ON admin_audit_log FROM {ADMIN_ROLE};
    -- جداول التشغيل الداخلية
    REVOKE INSERT, UPDATE, DELETE ON webhook_events, agent_runs FROM {ADMIN_ROLE};
    GRANT EXECUTE ON FUNCTION resolve_channel_account(text, text) TO {ADMIN_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {ADMIN_ROLE};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE USAGE, SELECT ON SEQUENCES FROM {ADMIN_ROLE};
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ADMIN_ROLE};
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ADMIN_ROLE};
    REVOKE EXECUTE ON FUNCTION resolve_channel_account(text, text) FROM {ADMIN_ROLE};
    DROP TABLE IF EXISTS admin_audit_log;
    ALTER TABLE channel_accounts DROP COLUMN IF EXISTS last_error;
    ALTER TABLE channel_accounts DROP COLUMN IF EXISTS last_checked_at;
    UPDATE channel_accounts SET status = 'paused' WHERE status = 'needs_reauth';
    ALTER TABLE channel_accounts DROP CONSTRAINT channel_accounts_status_check;
    ALTER TABLE channel_accounts ADD CONSTRAINT channel_accounts_status_check
        CHECK (status IN ('active','paused','disconnected'));
    DROP TABLE IF EXISTS admin_tokens;
    DROP TABLE IF EXISTS admin_users;
    """)
