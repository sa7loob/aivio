-- العزل المالي — الجزء 1 بدور app_user.
\set ON_ERROR_STOP on
\set QUIET on

-- (تجهيز من المالك: محادثة النور مستحقة الرد، راجع run_sql_tests.sh)
-- B9: الوكالة الموقوفة لا يرد عليها البوت (محادثة النور مستحقة في بيانات الاختبار)
DO $$
BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM claim_due_conversations(50) c
                      WHERE c.tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001'), 'B9: suspended tenant claimed';
  RAISE NOTICE 'PASS B9 suspended tenant: messages stored, bot silent';
END $$;

-- U1: كل وكالة ترى محفظتها وسنداتها واشتراكها فقط
DO $$
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  ASSERT (SELECT count(*) FROM wallets) = 1, 'U1: wallets';
  ASSERT NOT EXISTS (SELECT 1 FROM wallet_ledger WHERE tenant_id <> app_current_tenant_id()), 'U1: ledger leak';
  ASSERT (SELECT count(*) FROM receipts) >= 2, 'U1: own receipts';
  ASSERT (SELECT count(*) FROM subscriptions) = 1, 'U1: subscription';
  ASSERT (SELECT count(*) FROM manual_payments) = 0, 'U1: other tenant manual payment visible';
  ASSERT (SELECT count(*) FROM plans) >= 1, 'U1: plans catalog readable';
  RAISE NOTICE 'PASS U1 tenant sees only its own money';
END $$;

-- U2: لا تعديل مباشر، ولا وصول لدوال المشرف ولا للقسائم ولا لجداول الإدارة
DO $$
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  BEGIN
    PERFORM wallet_apply(app_current_tenant_id(), 'credit', 1000, 'cash', NULL, NULL, 'tenant_user', NULL);
    RAISE EXCEPTION 'U2: app_user called wallet_apply';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    UPDATE subscriptions SET status = 'active', current_period_end = now() + interval '10 years';
    RAISE EXCEPTION 'U2: app_user extended own subscription';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM 1 FROM vouchers LIMIT 1;
    RAISE EXCEPTION 'U2: app_user read vouchers';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM 1 FROM admin_tokens LIMIT 1;
    RAISE EXCEPTION 'U2: app_user read admin tokens';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM subscription_renew(app_current_tenant_id(), '11111111-0000-0000-0000-0000000000e1', 'tenant_user', NULL);
    RAISE EXCEPTION 'U2: app_user called subscription_renew';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  RAISE NOTICE 'PASS U2 app_user cannot move money, extend subscriptions or read vouchers/admin data';
END $$;

\echo 'BILLING USER PART 1 PASSED'
