-- إعادة تعيين كلمة المرور — بدور app_user بعد أن أصدر المشرف رابطين (صالح + منتهٍ) في run_sql_tests.sh
\set ON_ERROR_STOP on
\set QUIET on

DO $$
DECLARE uid uuid;
BEGIN
  uid := consume_password_reset('reset-ok', 'scrypt$new');
  ASSERT uid = '99999999-0000-0000-0000-000000000002', 'R1: wrong user';
  ASSERT (SELECT password_hash FROM users WHERE id = uid) = 'scrypt$new', 'R1: password not changed';
  ASSERT NOT EXISTS (SELECT 1 FROM user_sessions WHERE user_id = uid AND revoked_at IS NULL), 'R1: sessions not revoked';
  BEGIN
    PERFORM consume_password_reset('reset-ok', 'scrypt$again');
    RAISE EXCEPTION 'R1: token reused';
  EXCEPTION WHEN SQLSTATE 'AU003' THEN NULL; END;
  BEGIN
    PERFORM consume_password_reset('reset-old', 'scrypt$x');
    RAISE EXCEPTION 'R1: expired token accepted';
  EXCEPTION WHEN SQLSTATE 'AU003' THEN NULL; END;
  RAISE NOTICE 'PASS R1 reset link: single use, expiry enforced, all sessions revoked';
END $$;

\echo 'ALL PASSWORD RESET TESTS PASSED'
