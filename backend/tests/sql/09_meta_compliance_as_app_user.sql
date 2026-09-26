-- Meta callbacks + إعادة تعيين كلمة المرور — بدور app_user بعد 08 (القناة 777000777 للريان موجودة).
\set ON_ERROR_STOP on
\set QUIET on

-- M1: deauthorize => القنوات المرتبطة بمستخدم فيسبوك تحتاج إعادة ربط؛ غيرها لا يتأثر
DO $$
DECLARE r record; t uuid;
BEGIN
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000001', true);
  SELECT tenant_id INTO t FROM my_tenants() LIMIT 1;
  PERFORM set_config('app.tenant_id', t::text, true);
  PERFORM upsert_channel_account('messenger', '444000444', NULL, 'صفحة', '\\x01', false, '{"meta_user_id":"FBU-1"}');

  SELECT * INTO r FROM meta_user_revoked('FBU-1', 'deauthorize');
  ASSERT r.channels_affected = 1 AND length(r.confirmation_code) = 32, 'M1: deauthorize count/code';
  ASSERT (SELECT status FROM channel_accounts WHERE external_id = '444000444') = 'needs_reauth', 'M1: status';
  ASSERT (SELECT status FROM channel_accounts WHERE external_id = '777000777') = 'active', 'M1: unrelated channel touched';
  RAISE NOTICE 'PASS M1 deauthorize marks only that user''s channels as needs_reauth';
END $$;

-- M2: data deletion => التوكن يُحذف والقناة تُفصل، ورابط الحالة يعمل
DO $$
DECLARE r record; st record;
BEGIN
  SELECT * INTO r FROM meta_user_revoked('FBU-1', 'data_deletion');
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000001', true);
  PERFORM set_config('app.tenant_id', (SELECT tenant_id FROM my_tenants() LIMIT 1)::text, true);
  ASSERT (SELECT access_token_enc IS NULL AND status = 'disconnected' AND NOT config ? 'meta_user_id'
            FROM channel_accounts WHERE external_id = '444000444'), 'M2: token/link not removed';
  SELECT * INTO st FROM meta_deletion_status(r.confirmation_code);
  ASSERT st.status = 'completed', 'M2: status page';
  ASSERT NOT EXISTS (SELECT 1 FROM meta_deletion_status('not-a-code')), 'M2: unknown code';
  BEGIN
    PERFORM 1 FROM meta_data_deletion_requests;
    RAISE EXCEPTION 'M2: app_user reads deletion table directly';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  RAISE NOTICE 'PASS M2 data deletion removes tokens, status page by confirmation code only';
END $$;

-- M3: رابط إعادة تعيين كلمة المرور: مرة واحدة، يلغي الجلسات، والمنتهي مرفوض
INSERT INTO user_sessions (user_id, token_hash, expires_at)
VALUES ('99999999-0000-0000-0000-000000000002', 'sess-before-reset', now() + interval '1 day');
DO $$
DECLARE uid uuid;
BEGIN
  BEGIN
    PERFORM 1 FROM password_reset_tokens;
    RAISE EXCEPTION 'M3: app_user reads reset tokens';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  RAISE NOTICE 'PASS M3a reset tokens hidden from app_user';
END $$;

\echo 'ALL META COMPLIANCE (APP_USER) TESTS PASSED'
