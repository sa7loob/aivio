-- اختبارات محرك المال — يُشغَّل بدور app_admin (دور لوحة الإدارة).
\set ON_ERROR_STOP on
\set QUIET on

-- تجهيز: خطة + سعر + اشتراك تجريبي منتهي لوكالة النور
INSERT INTO plans (id, code, name, limits) VALUES
  ('11111111-0000-0000-0000-000000000001', 'basic', 'الأساسية', '{"conversations_month": 1000, "seats": 3}');
INSERT INTO plan_prices (id, plan_id, period_months, price_lyd) VALUES
  ('11111111-0000-0000-0000-0000000000e1', '11111111-0000-0000-0000-000000000001', 1, 150),
  ('11111111-0000-0000-0000-0000000000e3', '11111111-0000-0000-0000-000000000001', 3, 400);
INSERT INTO subscriptions (tenant_id, plan_id, status, current_period_end, renewal_price_id) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', '11111111-0000-0000-0000-000000000001', 'trialing',
   now() - interval '1 minute', '11111111-0000-0000-0000-0000000000e1');

-- B1: حتى المشرف لا يعدّل الأرصدة أو الدفتر مباشرة
DO $$
BEGIN
  BEGIN
    INSERT INTO wallets (tenant_id, balance_lyd) VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 1000000);
    RAISE EXCEPTION 'B1: admin inserted wallet directly';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    INSERT INTO wallet_ledger (tenant_id, direction, amount_lyd, balance_after, source, actor_type)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 1, 1, 'cash', 'admin');
    RAISE EXCEPTION 'B1: admin inserted ledger directly';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  RAISE NOTICE 'PASS B1 balances change only through wallet_apply()';
END $$;

-- B2: شحن + idempotency
DO $$
DECLARE e1 wallet_ledger; e2 wallet_ledger;
BEGIN
  e1 := wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 100.5, 'bank_transfer',
                     'cccccccc-0000-0000-0000-000000000001', 'تحويل', 'admin', NULL);
  e2 := wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 100.5, 'bank_transfer',
                     'cccccccc-0000-0000-0000-000000000001', 'تحويل', 'admin', NULL);
  ASSERT e1.id = e2.id, 'B2: replay created a second entry';
  ASSERT (SELECT balance_lyd FROM wallets WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') = 100.5, 'B2: balance';
  BEGIN
    PERFORM wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 999, 'bank_transfer',
                         'cccccccc-0000-0000-0000-000000000001', NULL, 'admin', NULL);
    RAISE EXCEPTION 'B2: conflicting replay accepted';
  EXCEPTION WHEN SQLSTATE 'BL002' THEN NULL; END;
  RAISE NOTICE 'PASS B2 credit is idempotent per (source, reference)';
END $$;

-- B3/B4: لا رصيد سالب، ولا مبالغ غير صالحة
DO $$
BEGIN
  BEGIN
    PERFORM wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'debit', 100.501, 'admin_adjustment', NULL, NULL, 'admin', NULL);
    RAISE EXCEPTION 'B3: overdraft allowed';
  EXCEPTION WHEN SQLSTATE 'BL001' THEN NULL; END;
  ASSERT (SELECT balance_lyd FROM wallets WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') = 100.5, 'B3: balance changed';
  BEGIN
    PERFORM wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 0.0005, 'cash', NULL, NULL, 'admin', NULL);
    RAISE EXCEPTION 'B4: sub-dirham amount accepted';
  EXCEPTION WHEN SQLSTATE 'BL003' THEN NULL; END;
  BEGIN
    PERFORM wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', -5, 'cash', NULL, NULL, 'admin', NULL);
    RAISE EXCEPTION 'B4: negative amount accepted';
  EXCEPTION WHEN SQLSTATE 'BL003' THEN NULL; END;
  RAISE NOTICE 'PASS B3/B4 no overdraft, amounts validated to the dirham';
END $$;

-- B5: سندات قبض تسلسلية
DO $$
DECLARE r1 receipts; r2 receipts; r1b receipts; e wallet_ledger; y text := extract(year FROM now() AT TIME ZONE 'Africa/Tripoli')::text;
BEGIN
  r1 := issue_receipt((SELECT id FROM wallet_ledger WHERE reference_id = 'cccccccc-0000-0000-0000-000000000001'), 'TRX-1', NULL);
  ASSERT r1.number = 'RC-' || y || '-000001', format('B5: first number %s', r1.number);
  r1b := issue_receipt(r1.ledger_id, 'TRX-1', NULL);
  ASSERT r1b.id = r1.id, 'B5: receipt not idempotent';
  e := wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 99.5, 'cash', NULL, NULL, 'admin', NULL);
  r2 := issue_receipt(e.id, NULL, NULL);
  ASSERT r2.number = 'RC-' || y || '-000002', 'B5: sequence';
  RAISE NOTICE 'PASS B5 gapless receipt numbers %, %', r1.number, r2.number;
END $$;

-- B6: التجديد من المحفظة (الرصيد الآن 200)
DO $$
DECLARE p subscription_periods; s subscriptions; p2 subscription_periods;
BEGIN
  p := subscription_renew('aaaaaaaa-0000-0000-0000-000000000001', '11111111-0000-0000-0000-0000000000e1', 'admin', NULL);
  SELECT * INTO s FROM subscriptions WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001';
  ASSERT s.status = 'active', 'B6: status';
  ASSERT s.current_period_end = p.period_end, 'B6: period end';
  ASSERT p.period_end BETWEEN now() + interval '27 days' AND now() + interval '32 days', 'B6: one month';
  ASSERT (SELECT balance_lyd FROM wallets WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') = 50, 'B6: debit';
  -- تجديد مبكر (رصيد 50 لا يكفي 150)
  BEGIN
    PERFORM subscription_renew('aaaaaaaa-0000-0000-0000-000000000001', '11111111-0000-0000-0000-0000000000e1', 'admin', NULL);
    RAISE EXCEPTION 'B7: renewed without funds';
  EXCEPTION WHEN SQLSTATE 'BL001' THEN NULL; END;
  ASSERT (SELECT current_period_end FROM subscriptions WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') = p.period_end, 'B7: changed';
  -- شحن ثم تجديد مبكر: يبدأ من نهاية الفترة الحالية
  PERFORM wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 150, 'cash', NULL, NULL, 'admin', NULL);
  p2 := subscription_renew('aaaaaaaa-0000-0000-0000-000000000001', '11111111-0000-0000-0000-0000000000e1', 'admin', NULL);
  ASSERT p2.period_start = p.period_end, 'B6: early renewal must stack';
  RAISE NOTICE 'PASS B6/B7 renew debits wallet, stacks early renewals, refuses without funds';
END $$;

-- B8: دورة الحياة: انتهاء بدون رصيد => مهلة => إيقاف => البوت لا يرد => شحن => تفعيل تلقائي
UPDATE subscriptions SET current_period_end = now() - interval '1 second'
 WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001';
DO $$
DECLARE r record;
BEGIN
  SELECT * INTO r FROM subscription_lifecycle_tick() t WHERE t.tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001';
  ASSERT r.new_status = 'grace', format('B8: expected grace, got %s', r.new_status);
  UPDATE subscriptions SET grace_until = now() - interval '1 second' WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001';
  SELECT * INTO r FROM subscription_lifecycle_tick() t WHERE t.tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001';
  ASSERT r.new_status = 'suspended', 'B8: expected suspended';
  RAISE NOTICE 'PASS B8 lifecycle active -> grace -> suspended';
END $$;

\echo 'BILLING ADMIN PART 1 PASSED'
