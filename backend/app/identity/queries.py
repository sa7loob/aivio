"""SQL for identity, sessions, team and onboarding (app_user role)."""
from sqlalchemy import text

# ------------------------------------------------------------------ rate limiting (SYSTEM)
RECENT_FAILURES = text("""
    SELECT count(*) FILTER (WHERE email_lower = :email) AS by_email,
           count(*) FILTER (WHERE ip = CAST(:ip AS inet))  AS by_ip
      FROM auth_attempts
     WHERE NOT success AND created_at > now() - interval '15 minutes'
       AND (email_lower = :email OR ip = CAST(:ip AS inet))
""")

RECORD_ATTEMPT = text("""
    INSERT INTO auth_attempts (email_lower, ip, success) VALUES (:email, CAST(:ip AS inet), :success)
""")

# ------------------------------------------------------------------ users & sessions (SYSTEM)
INSERT_USER = text("""
    INSERT INTO users (email, full_name, phone_e164, password_hash)
    VALUES (:email, :full_name, :phone, :password_hash)
    RETURNING id
""")

USER_BY_EMAIL = text("""
    SELECT id, email, full_name, password_hash, is_active FROM users WHERE lower(email) = lower(:email)
""")

UPDATE_PASSWORD = text("UPDATE users SET password_hash = :h WHERE id = :id")

TOUCH_LOGIN = text("UPDATE users SET last_login_at = now() WHERE id = :id")

INSERT_SESSION = text("""
    INSERT INTO user_sessions (user_id, token_hash, expires_at, ip, user_agent)
    VALUES (:user_id, :token_hash, now() + make_interval(days => :days), CAST(:ip AS inet), :ua)
""")

# جلسة صالحة => المستخدم. last_seen_at يُحدَّث مرة كل 5 دقائق كحد أقصى (تخفيف الكتابة)
RESOLVE_SESSION = text("""
    WITH s AS (
        SELECT us.id, us.user_id FROM user_sessions us
          JOIN users u ON u.id = us.user_id AND u.is_active
         WHERE us.token_hash = :h AND us.revoked_at IS NULL AND us.expires_at > now()
    ), touch AS (
        UPDATE user_sessions SET last_seen_at = now()
         WHERE id = (SELECT id FROM s) AND last_seen_at < now() - interval '5 minutes'
    )
    SELECT u.id, u.email, u.full_name, u.phone_e164, s.id AS session_id
      FROM s JOIN users u ON u.id = s.user_id
""")

REVOKE_SESSION = text("UPDATE user_sessions SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL")

REVOKE_ALL_SESSIONS = text("""
    UPDATE user_sessions SET revoked_at = now()
     WHERE user_id = :user_id AND revoked_at IS NULL AND (CAST(:keep AS uuid) IS NULL OR id <> CAST(:keep AS uuid))
""")

# ------------------------------------------------------------------ tenancy (USER context)
REGISTER_TENANT = text("""
    SELECT register_tenant(:slug, :name, :business_type, :plan_code, :trial_days, :owner_phone)
""")

MY_TENANTS = text("SELECT tenant_id, name, slug, role, subscription_status FROM my_tenants()")

MEMBERSHIP = text("""
    SELECT role FROM memberships
     WHERE tenant_id = :tenant_id AND user_id = app_current_user_id() AND status = 'active'
""")

ACCEPT_INVITATION = text("SELECT tenant_id, role FROM accept_invitation(:h)")

# ------------------------------------------------------------------ team (TENANT context)
LIST_MEMBERS = text("""
    SELECT m.user_id, u.full_name, u.email, m.role, m.status, m.created_at
      FROM memberships m JOIN users u ON u.id = m.user_id
     WHERE m.tenant_id = app_current_tenant_id()
     ORDER BY m.created_at
""")

CREATE_INVITATION = text("""
    INSERT INTO invitations (tenant_id, role, token_hash, email, note, invited_by, expires_at)
    VALUES (app_current_tenant_id(), :role, :h, :email, :note, app_current_user_id(),
            now() + make_interval(days => :days))
    RETURNING id, expires_at
""")

LIST_INVITATIONS = text("""
    SELECT id, role, email, note, created_at, expires_at, accepted_at, revoked_at
      FROM invitations ORDER BY created_at DESC LIMIT 100
""")

REVOKE_INVITATION = text("""
    UPDATE invitations SET revoked_at = now()
     WHERE id = :id AND accepted_at IS NULL AND revoked_at IS NULL RETURNING id
""")

# ------------------------------------------------------------------ channels (TENANT context)
LIST_CHANNELS = text("""
    SELECT id, channel, external_id, display_name, status, is_test, last_checked_at, last_error, created_at
      FROM channel_accounts ORDER BY created_at
""")

CHANNEL_FOR_UPDATE = text("""
    SELECT id, channel, external_id, waba_id, access_token_enc, config, status
      FROM channel_accounts WHERE id = :id FOR UPDATE
""")

SET_CHANNEL_STATUS = text("""
    UPDATE channel_accounts SET status = :status, last_error = :error, last_checked_at = now()
     WHERE id = :id
""")

UPSERT_CHANNEL = text("""
    SELECT channel_account_id, outcome
      FROM upsert_channel_account(:channel, :external_id, :waba_id, :display_name, :token, :is_test,
                                  CAST(:config AS jsonb))
""")

# ------------------------------------------------------------------ onboarding sessions (TENANT)
CREATE_ONBOARDING = text("""
    INSERT INTO onboarding_sessions (tenant_id, user_id, flow, state_hash, expires_at)
    VALUES (app_current_tenant_id(), app_current_user_id(), :flow, :state_hash,
            now() + make_interval(mins => :minutes))
    RETURNING id, expires_at
""")

ONBOARDING_FOR_UPDATE = text("""
    SELECT id, flow, state_hash, status, step, meta, token_enc, error, expires_at, (expires_at < now()) AS expired
      FROM onboarding_sessions WHERE id = :id FOR UPDATE
""")

GET_ONBOARDING = text("""
    SELECT id, flow, status, step, meta, error, created_at, expires_at FROM onboarding_sessions WHERE id = :id
""")

UPDATE_ONBOARDING = text("""
    UPDATE onboarding_sessions
       SET status = :status, step = :step, error = :error,
           meta = meta || CAST(:meta AS jsonb),
           token_enc = CASE WHEN :clear_token THEN NULL ELSE coalesce(:token_enc, token_enc) END,
           expires_at = CASE WHEN CAST(:extend_minutes AS int) IS NULL THEN expires_at
                             ELSE now() + make_interval(mins => CAST(:extend_minutes AS int)) END
     WHERE id = :id
""")
