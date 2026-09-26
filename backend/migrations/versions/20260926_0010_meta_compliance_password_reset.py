"""Meta compliance callbacks (deauthorize / data deletion) + support-issued password reset.

- Meta تتطلب لمراجعة التطبيق: Deauthorize callback و Data Deletion callback (مع رابط حالة + رمز تأكيد).
  نربط القنوات بمستخدم فيسبوك الذي ربطها عبر config.meta_user_id (معرّف خاص بالتطبيق).
- إعادة تعيين كلمة المرور بدون بريد: الدعم يصدر رابطاً لمرة واحدة (hash فقط) ويرسله للمالك عبر واتساب.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-26
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE meta_data_deletion_requests (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        meta_user_id      text NOT NULL,
        confirmation_code text NOT NULL UNIQUE,
        kind              text NOT NULL CHECK (kind IN ('deauthorize','data_deletion')),
        status            text NOT NULL DEFAULT 'completed' CHECK (status IN ('received','completed')),
        channels_affected int  NOT NULL DEFAULT 0,
        created_at        timestamptz NOT NULL DEFAULT now(),
        completed_at      timestamptz
    );
    CREATE INDEX meta_deletion_user_idx ON meta_data_deletion_requests (meta_user_id);

    CREATE TABLE password_reset_tokens (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash text NOT NULL UNIQUE,
        issued_by  uuid REFERENCES admin_users(id) ON DELETE SET NULL,
        expires_at timestamptz NOT NULL,
        used_at    timestamptz,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX password_reset_user_idx ON password_reset_tokens (user_id);
    """)
    # دور التشغيل: لا وصول مباشر (الدوال فقط)؛ المشرف يصدر الروابط
    op.execute("""
    REVOKE ALL ON meta_data_deletion_requests, password_reset_tokens FROM app_user;
    REVOKE UPDATE, DELETE ON meta_data_deletion_requests FROM app_admin;
    """)

    op.execute("""
    -- Meta: المستخدم ألغى صلاحيات التطبيق أو طلب حذف بياناته.
    -- deauthorize => القنوات التي ربطها تحتاج إعادة ربط، ونمسح توكناتها المؤقتة.
    -- data_deletion => نحذف التوكنات نهائياً ونفصل القنوات (سجل المحادثات يبقى ملكاً للنشاط التجاري).
    CREATE FUNCTION meta_user_revoked(p_meta_user_id text, p_kind text)
    RETURNS TABLE (confirmation_code text, channels_affected int)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE n int; v_code text := replace(gen_random_uuid()::text, '-', '');   -- عشوائي آمن بدون pgcrypto
    BEGIN
        IF p_kind NOT IN ('deauthorize','data_deletion') OR coalesce(p_meta_user_id, '') = '' THEN
            RAISE EXCEPTION 'invalid_request' USING ERRCODE = 'BL003';
        END IF;
        IF p_kind = 'deauthorize' THEN
            UPDATE channel_accounts SET status = 'needs_reauth', last_error = 'meta: app deauthorized by user',
                   last_checked_at = now()
             WHERE config->>'meta_user_id' = p_meta_user_id AND status = 'active';
        ELSE
            UPDATE channel_accounts SET status = 'disconnected', access_token_enc = NULL,
                   last_error = 'meta: data deletion requested', last_checked_at = now(),
                   config = config - 'meta_user_id' - 'pin_enc'
             WHERE config->>'meta_user_id' = p_meta_user_id;
        END IF;
        GET DIAGNOSTICS n = ROW_COUNT;
        UPDATE onboarding_sessions SET token_enc = NULL WHERE meta->>'meta_user_id' = p_meta_user_id;
        INSERT INTO meta_data_deletion_requests (meta_user_id, confirmation_code, kind, status,
                                                 channels_affected, completed_at)
        VALUES (p_meta_user_id, v_code, p_kind, 'completed', n, now());
        RETURN QUERY SELECT v_code, n;
    END $$;

    -- صفحة حالة الحذف (رابط تعطيه Meta للمستخدم): لا تكشف إلا الحالة والتاريخ
    CREATE FUNCTION meta_deletion_status(p_code text)
    RETURNS TABLE (status text, created_at timestamptz, completed_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT status, created_at, completed_at FROM meta_data_deletion_requests
         WHERE confirmation_code = p_code AND kind = 'data_deletion'
    $$;

    -- استهلاك رابط إعادة التعيين: مرة واحدة، غير منتهٍ، ثم إلغاء كل الجلسات
    CREATE FUNCTION consume_password_reset(p_token_hash text, p_new_hash text)
    RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE t password_reset_tokens;
    BEGIN
        SELECT * INTO t FROM password_reset_tokens
         WHERE token_hash = p_token_hash AND used_at IS NULL AND expires_at > now()
         FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'invalid_reset_token' USING ERRCODE = 'AU003'; END IF;
        UPDATE password_reset_tokens SET used_at = now() WHERE id = t.id;
        UPDATE password_reset_tokens SET used_at = now() WHERE user_id = t.user_id AND used_at IS NULL;
        UPDATE users SET password_hash = p_new_hash WHERE id = t.user_id AND is_active;
        IF NOT FOUND THEN RAISE EXCEPTION 'invalid_reset_token' USING ERRCODE = 'AU003'; END IF;
        UPDATE user_sessions SET revoked_at = now() WHERE user_id = t.user_id AND revoked_at IS NULL;
        RETURN t.user_id;
    END $$;

    REVOKE ALL ON FUNCTION meta_user_revoked(text, text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION meta_deletion_status(text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION consume_password_reset(text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION meta_user_revoked(text, text) TO app_user;
    GRANT EXECUTE ON FUNCTION meta_deletion_status(text) TO app_user;
    GRANT EXECUTE ON FUNCTION consume_password_reset(text, text) TO app_user;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS consume_password_reset(text, text);
    DROP FUNCTION IF EXISTS meta_deletion_status(text);
    DROP FUNCTION IF EXISTS meta_user_revoked(text, text);
    DROP TABLE IF EXISTS password_reset_tokens;
    DROP TABLE IF EXISTS meta_data_deletion_requests;
    """)
