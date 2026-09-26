-- العزل المالي — الجزء 2 بدور app_user.
\set ON_ERROR_STOP on
\set QUIET on

-- U2b: بعد إعادة التفعيل (B10) يعود البوت للرد على نفس المحادثة (ضابط إيجابي لـ B9)
DO $$
BEGIN
  ASSERT EXISTS (SELECT 1 FROM claim_due_conversations(50) c
                  WHERE c.tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001'), 'U2b: reactivated tenant not claimed';
  RAISE NOTICE 'PASS U2b reactivated tenant: bot replies again';
END $$;

-- U3: الاسترداد من لوحة الوكالة (المرحلة 6) يعمل فقط داخل سياق وكالة
DO $$
DECLARE r record;
BEGIN
  BEGIN
    PERFORM redeem_voucher('hash-ok-2', NULL, 'tenant_user', NULL);
    RAISE EXCEPTION 'U3: redeem without tenant context';
  EXCEPTION WHEN SQLSTATE 'BL004' THEN NULL; END;
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  SELECT * INTO r FROM redeem_voucher('hash-ok-2', '10.0.0.9', 'tenant_user', NULL);
  ASSERT r.ok AND r.amount_lyd = 50, 'U3: tenant redeem';
  RAISE NOTICE 'PASS U3 tenant redeems voucher via SECURITY DEFINER function only';
END $$;

-- U4: الـ worker يشغّل المهمة الدورية
DO $$
BEGIN
  PERFORM * FROM subscription_lifecycle_tick();
  RAISE NOTICE 'PASS U4 lifecycle tick callable by worker role';
END $$;

\echo 'ALL BILLING (APP_USER) TESTS PASSED'
