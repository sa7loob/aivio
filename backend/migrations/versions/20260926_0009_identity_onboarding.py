"""identity + self-serve onboarding: users, sessions, memberships, invitations, onboarding sessions,
and the DB functions that cross tenant boundaries safely.

القرارات:
  - users / user_sessions عامة (ليست تابعة لوكالة): شخص واحد قد يعمل مع أكثر من نشاط.
  - memberships تحت RLS بسياسة مزدوجة: ترى صفوف الوكالة الحالية، أو عضوياتك أنت (app.user_id).
  - إنشاء وكالة / قبول دعوة / حجز قناة = دوال SECURITY DEFINER تتحقق من app.user_id و app.tenant_id
    (دور التشغيل لا يملك INSERT على tenants ولا على memberships).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION app_current_user_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE AS $$
        SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
    $$;
    """)

    # ------------------------------------------------------------------ users & sessions
    op.execute("""
    CREATE TABLE users (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email         text NOT NULL CHECK (email ~ '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'),
        full_name     text NOT NULL,
        phone_e164    text CHECK (phone_e164 ~ '^\\+[1-9][0-9]{7,14}$'),
        password_hash text NOT NULL,
        is_active     boolean NOT NULL DEFAULT true,
        created_at    timestamptz NOT NULL DEFAULT now(),
        last_login_at timestamptz
    );
    CREATE UNIQUE INDEX users_email_uq ON users (lower(email));

    -- جلسات opaque: يُخزَّن hash التوكن فقط؛ الإلغاء فوري (logout / تعطيل المستخدم)
    CREATE TABLE user_sessions (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash   text NOT NULL UNIQUE,
        created_at   timestamptz NOT NULL DEFAULT now(),
        expires_at   timestamptz NOT NULL,
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        revoked_at   timestamptz,
        ip           inet,
        user_agent   text
    );
    CREATE INDEX user_sessions_user_idx ON user_sessions (user_id) WHERE revoked_at IS NULL;

    -- حد محاولات الدخول (بالبريد وبالـ IP)
    CREATE TABLE auth_attempts (
        id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        email_lower text,
        ip          inet,
        success     boolean NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX auth_attempts_email_idx ON auth_attempts (email_lower, created_at DESC);
    CREATE INDEX auth_attempts_ip_idx    ON auth_attempts (ip, created_at DESC);
    """)
    op.execute("REVOKE DELETE ON users FROM app_user")
    op.execute("""
    REVOKE ALL ON user_sessions, auth_attempts FROM app_admin;
    REVOKE INSERT, DELETE ON users FROM app_admin;
    """)

    # ------------------------------------------------------------------ memberships
    op.execute("""
    CREATE TABLE memberships (
        tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role       text NOT NULL CHECK (role IN ('owner','admin','agent')),
        status     text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, user_id)
    );
    CREATE INDEX memberships_user_idx ON memberships (user_id);

    ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
    CREATE POLICY membership_visibility ON memberships
        USING (tenant_id = app_current_tenant_id() OR user_id = app_current_user_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

    -- ربط الموظف (مستقبل إشعارات الـ Leads) بحساب الدخول
    ALTER TABLE staff_users ADD COLUMN user_id uuid REFERENCES users(id) ON DELETE SET NULL;
    CREATE UNIQUE INDEX staff_users_tenant_user_uq ON staff_users (tenant_id, user_id) WHERE user_id IS NOT NULL;
    """)
    op.execute("REVOKE INSERT ON memberships FROM app_user")   # عبر register_tenant / accept_invitation فقط

    # ------------------------------------------------------------------ invitations
    op.execute("""
    CREATE TABLE invitations (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        role        text NOT NULL CHECK (role IN ('owner','admin','agent')),  -- owner: من لوحة الإدارة فقط
        token_hash  text NOT NULL UNIQUE,
        email       text,
        note        text,
        invited_by  uuid REFERENCES users(id) ON DELETE SET NULL,
        expires_at  timestamptz NOT NULL,
        accepted_at timestamptz,
        accepted_by uuid REFERENCES users(id) ON DELETE SET NULL,
        revoked_at  timestamptz,
        created_at  timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX invitations_tenant_idx ON invitations (tenant_id, created_at DESC);
    """)
    op.execute("SELECT app_enable_tenant_rls('invitations')")

    # ------------------------------------------------------------------ onboarding sessions
    op.execute("""
    CREATE TABLE onboarding_sessions (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
        flow       text NOT NULL CHECK (flow IN ('whatsapp_embedded_signup','facebook_login')),
        state_hash text NOT NULL,
        status     text NOT NULL DEFAULT 'started'
                   CHECK (status IN ('started','token_exchanged','completed','failed','expired')),
        step       text,
        meta       jsonb NOT NULL DEFAULT '{}'::jsonb,
        token_enc  bytea,                  -- توكن مؤقت بين الخطوات (يُمسح عند الاكتمال)
        error      text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL
    );
    CREATE INDEX onboarding_sessions_tenant_idx ON onboarding_sessions (tenant_id, created_at DESC);
    CREATE TRIGGER onboarding_sessions_set_updated_at BEFORE UPDATE ON onboarding_sessions
        FOR EACH ROW EXECUTE FUNCTION app_set_updated_at();
    """)
    op.execute("SELECT app_enable_tenant_rls('onboarding_sessions')")

    # ------------------------------------------------------------------ functions
    op.execute("""
    -- التسجيل الذاتي: وكالة + عضوية مالك + موظف للإشعارات + اشتراك تجريبي، في transaction واحدة
    CREATE FUNCTION register_tenant(p_slug text, p_name text, p_business_type text,
                                    p_plan_code text, p_trial_days int, p_owner_phone text)
    RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE
        u users; v_tenant uuid; v_plan uuid; v_price uuid;
    BEGIN
        SELECT * INTO u FROM users WHERE id = app_current_user_id() AND is_active;
        IF NOT FOUND THEN RAISE EXCEPTION 'user_context_required' USING ERRCODE = 'AU001'; END IF;
        IF p_trial_days NOT BETWEEN 0 AND 90 THEN RAISE EXCEPTION 'invalid_trial' USING ERRCODE = 'BL003'; END IF;

        SELECT p.id, (SELECT pp.id FROM plan_prices pp WHERE pp.plan_id = p.id AND pp.is_active
                       ORDER BY pp.period_months LIMIT 1)
          INTO v_plan, v_price
          FROM plans p WHERE p.code = p_plan_code AND p.is_active;
        IF v_plan IS NULL THEN RAISE EXCEPTION 'plan_price_unavailable' USING ERRCODE = 'BL005'; END IF;

        INSERT INTO tenants (slug, name, business_type, status)
        VALUES (p_slug, p_name, p_business_type, 'trial') RETURNING id INTO v_tenant;
        INSERT INTO memberships (tenant_id, user_id, role) VALUES (v_tenant, u.id, 'owner');
        INSERT INTO staff_users (tenant_id, user_id, full_name, email, whatsapp_phone, role, notify_on_new_lead)
        VALUES (v_tenant, u.id, u.full_name, u.email, coalesce(p_owner_phone, u.phone_e164), 'owner', true);
        INSERT INTO subscriptions (tenant_id, plan_id, status, current_period_end, renewal_price_id)
        VALUES (v_tenant, v_plan, 'trialing', now() + make_interval(days => p_trial_days), v_price);
        RETURN v_tenant;
    END $$;

    -- قبول دعوة: التوكن (hash) يحدد الوكالة؛ المستخدم من app.user_id
    CREATE FUNCTION accept_invitation(p_token_hash text)
    RETURNS TABLE (tenant_id uuid, role text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    #variable_conflict use_column
    DECLARE u users; inv invitations;
    BEGIN
        SELECT * INTO u FROM users WHERE id = app_current_user_id() AND is_active;
        IF NOT FOUND THEN RAISE EXCEPTION 'user_context_required' USING ERRCODE = 'AU001'; END IF;
        SELECT * INTO inv FROM invitations i
         WHERE i.token_hash = p_token_hash AND i.revoked_at IS NULL AND i.expires_at > now()
         FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'invalid_invitation' USING ERRCODE = 'AU002'; END IF;
        IF inv.accepted_at IS NOT NULL AND inv.accepted_by <> u.id THEN
            RAISE EXCEPTION 'invalid_invitation' USING ERRCODE = 'AU002';
        END IF;

        INSERT INTO memberships AS m (tenant_id, user_id, role)
        VALUES (inv.tenant_id, u.id, inv.role)
        ON CONFLICT ON CONSTRAINT memberships_pkey DO UPDATE SET status = 'active';
        -- مكتب سُجّل يدوياً (المرحلة 4.5) له موظف بنفس البريد/الهاتف => نربطه بدل تكرار الإشعارات
        UPDATE staff_users SET user_id = u.id
         WHERE id = (SELECT st.id FROM staff_users st
                      WHERE st.tenant_id = inv.tenant_id AND st.user_id IS NULL
                        AND (lower(st.email) = lower(u.email)
                             OR (u.phone_e164 IS NOT NULL AND st.whatsapp_phone = u.phone_e164))
                      ORDER BY st.created_at LIMIT 1)
           AND NOT EXISTS (SELECT 1 FROM staff_users x WHERE x.tenant_id = inv.tenant_id AND x.user_id = u.id);
        IF NOT FOUND THEN
            INSERT INTO staff_users AS s (tenant_id, user_id, full_name, email, whatsapp_phone, role, notify_on_new_lead)
            VALUES (inv.tenant_id, u.id, u.full_name, u.email, u.phone_e164,
                    CASE inv.role WHEN 'owner' THEN 'owner' WHEN 'admin' THEN 'manager' ELSE 'agent' END,
                    inv.role = 'owner')
            ON CONFLICT (tenant_id, user_id) WHERE user_id IS NOT NULL DO NOTHING;
        END IF;
        UPDATE invitations SET accepted_at = coalesce(accepted_at, now()), accepted_by = u.id WHERE id = inv.id;
        RETURN QUERY SELECT inv.tenant_id, inv.role;
    END $$;

    -- الوكالات التي ينتمي لها المستخدم الحالي (قبل اختيار وكالة: RLS على tenants تمنع الرؤية)
    CREATE FUNCTION my_tenants()
    RETURNS TABLE (tenant_id uuid, name text, slug text, role text, subscription_status text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT t.id, t.name, t.slug, m.role, s.status
          FROM memberships m
          JOIN tenants t ON t.id = m.tenant_id
          LEFT JOIN subscriptions s ON s.tenant_id = t.id
         WHERE m.user_id = app_current_user_id() AND m.status = 'active'
           AND t.status NOT IN ('cancelled')
         ORDER BY t.name
    $$;

    -- حجز قناة للوكالة الحالية. لا يكشف أي شيء عن وكالة أخرى تملك نفس الرقم/الصفحة.
    CREATE FUNCTION upsert_channel_account(p_channel text, p_external_id text, p_waba_id text,
                                           p_display_name text, p_token bytea, p_is_test boolean,
                                           p_config jsonb)
    RETURNS TABLE (channel_account_id uuid, outcome text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE t uuid := app_current_tenant_id(); ca channel_accounts;
    BEGIN
        IF t IS NULL THEN RAISE EXCEPTION 'tenant_context_required' USING ERRCODE = 'BL004'; END IF;
        SELECT * INTO ca FROM channel_accounts c
         WHERE c.channel = p_channel AND c.external_id = p_external_id FOR UPDATE;
        IF FOUND AND ca.tenant_id <> t THEN
            RETURN QUERY SELECT NULL::uuid, 'conflict'::text;
            RETURN;
        END IF;
        IF FOUND THEN
            UPDATE channel_accounts
               SET access_token_enc = p_token, waba_id = coalesce(p_waba_id, waba_id),
                   display_name = coalesce(p_display_name, display_name), is_test = p_is_test,
                   status = 'active', last_error = NULL, last_checked_at = now(),
                   config = config || coalesce(p_config, '{}'::jsonb)
             WHERE id = ca.id;
            RETURN QUERY SELECT ca.id, 'updated'::text;
        ELSE
            INSERT INTO channel_accounts (tenant_id, channel, external_id, waba_id, display_name,
                                          access_token_enc, is_test, status, config, last_checked_at)
            VALUES (t, p_channel, p_external_id, p_waba_id, p_display_name, p_token, p_is_test,
                    'active', coalesce(p_config, '{}'::jsonb), now())
            RETURNING id INTO ca.id;
            RETURN QUERY SELECT ca.id, 'created'::text;
        END IF;
    END $$;

    -- مراقبة صحة التوكنات: الـ worker يحجز قنوات لم تُفحص منذ p_every (lease عبر last_checked_at)
    CREATE FUNCTION channels_due_for_health_check(p_limit int DEFAULT 20, p_every interval DEFAULT '6 hours')
    RETURNS TABLE (channel_account_id uuid, tenant_id uuid, channel text)
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        WITH due AS (
            SELECT c.id FROM channel_accounts c
             WHERE c.status = 'active'
               AND (c.last_checked_at IS NULL OR c.last_checked_at < now() - p_every)
             ORDER BY c.last_checked_at NULLS FIRST
             LIMIT p_limit FOR UPDATE SKIP LOCKED
        )
        UPDATE channel_accounts c SET last_checked_at = now()
          FROM due WHERE c.id = due.id
        RETURNING c.id, c.tenant_id, c.channel
    $$;

    REVOKE ALL ON FUNCTION register_tenant(text, text, text, text, int, text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION accept_invitation(text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION my_tenants() FROM PUBLIC;
    REVOKE ALL ON FUNCTION upsert_channel_account(text, text, text, text, bytea, boolean, jsonb) FROM PUBLIC;
    REVOKE ALL ON FUNCTION channels_due_for_health_check(int, interval) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION register_tenant(text, text, text, text, int, text) TO app_user;
    GRANT EXECUTE ON FUNCTION accept_invitation(text) TO app_user;
    GRANT EXECUTE ON FUNCTION my_tenants() TO app_user;
    GRANT EXECUTE ON FUNCTION upsert_channel_account(text, text, text, text, bytea, boolean, jsonb) TO app_user;
    GRANT EXECUTE ON FUNCTION channels_due_for_health_check(int, interval) TO app_user;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS channels_due_for_health_check(int, interval);
    DROP FUNCTION IF EXISTS upsert_channel_account(text, text, text, text, bytea, boolean, jsonb);
    DROP FUNCTION IF EXISTS my_tenants();
    DROP FUNCTION IF EXISTS accept_invitation(text);
    DROP FUNCTION IF EXISTS register_tenant(text, text, text, text, int, text);
    DROP TABLE IF EXISTS onboarding_sessions;
    DROP TABLE IF EXISTS invitations;
    DROP INDEX IF EXISTS staff_users_tenant_user_uq;
    ALTER TABLE staff_users DROP COLUMN IF EXISTS user_id;
    DROP TABLE IF EXISTS memberships;
    DROP TABLE IF EXISTS auth_attempts;
    DROP TABLE IF EXISTS user_sessions;
    DROP TABLE IF EXISTS users;
    DROP FUNCTION IF EXISTS app_current_user_id();
    """)
