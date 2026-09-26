-- اختبارات محرك المال — الجزء 2 بدور app_admin.
\set ON_ERROR_STOP on
\set QUIET on

-- B10: شحن ثم try_autorenew => active
DO $$
BEGIN
  ASSERT NOT subscription_try_autorenew('aaaaaaaa-0000-0000-0000-000000000001'), 'B10: renewed with 50 LYD';
  PERFORM wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 100, 'cash', NULL, NULL, 'admin', NULL);
  ASSERT subscription_try_autorenew('aaaaaaaa-0000-0000-0000-000000000001'), 'B10: not renewed';
  ASSERT (SELECT status FROM subscriptions WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') = 'active', 'B10';
  ASSERT (SELECT current_period_end > now() + interval '27 days' FROM subscriptions
           WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001'), 'B10: reactivated period starts now';
  RAISE NOTICE 'PASS B10 top-up reactivates suspended subscription automatically';
END $$;

-- B11: القسائم
INSERT INTO voucher_batches (id, distributor_name, denomination_lyd, quantity) VALUES
  ('22222222-0000-0000-0000-000000000001', 'موزع تجريبي', 50, 3),
  ('22222222-0000-0000-0000-000000000002', 'دفعة ملغاة', 50, 1);
INSERT INTO vouchers (batch_id, code_hash, last4, amount_lyd) VALUES
  ('22222222-0000-0000-0000-000000000001', 'hash-ok-1', '1111', 50),
  ('22222222-0000-0000-0000-000000000001', 'hash-ok-2', '2222', 50),
  ('22222222-0000-0000-0000-000000000002', 'hash-void', '3333', 50);
UPDATE voucher_batches SET status = 'void', void_reason = 'تسريب', voided_at = now()
 WHERE id = '22222222-0000-0000-0000-000000000002';
DO $$
DECLARE r record; bal numeric;
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  bal := (SELECT balance_lyd FROM wallets WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001');
  SELECT * INTO r FROM redeem_voucher('hash-ok-1', '10.0.0.1', 'admin', NULL);
  ASSERT r.ok AND r.amount_lyd = 50 AND r.balance_lyd = bal + 50, 'B11: redeem';
  SELECT * INTO r FROM redeem_voucher('hash-ok-1', NULL, 'admin', NULL);
  ASSERT r.ok AND r.reason = 'already_redeemed_by_you', 'B11: double click';
  ASSERT (SELECT balance_lyd FROM wallets WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') = bal + 50, 'B11: double credit';
  SELECT * INTO r FROM redeem_voucher('hash-void', NULL, 'admin', NULL);
  ASSERT NOT r.ok AND r.reason = 'invalid_or_used', 'B11: void batch redeemed';

  PERFORM set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', true);
  SELECT * INTO r FROM redeem_voucher('hash-ok-1', NULL, 'admin', NULL);
  ASSERT NOT r.ok AND r.reason = 'invalid_or_used', 'B11: used voucher redeemed by other tenant';
  FOR i IN 1..4 LOOP PERFORM redeem_voucher('guess-' || i, NULL, 'admin', NULL); END LOOP;
  SELECT * INTO r FROM redeem_voucher('hash-ok-2', NULL, 'admin', NULL);
  ASSERT NOT r.ok AND r.reason = 'too_many_attempts', 'B11: rate limit (valid code blocked after 5 failures)';
  RAISE NOTICE 'PASS B11 vouchers: redeem once, idempotent, void batches, brute-force limit';
END $$;

-- B12: التحويل اليدوي: الموافقة مرتين لا تشحن مرتين
DO $$
DECLARE mp uuid; e1 wallet_ledger; e2 wallet_ledger;
BEGIN
  INSERT INTO manual_payments (tenant_id, method, amount_lyd, sender_phone, bank_reference)
  VALUES ('bbbbbbbb-0000-0000-0000-000000000002', 'libyana_balance', 75, '+218925556666', NULL)
  RETURNING id INTO mp;
  e1 := wallet_apply('bbbbbbbb-0000-0000-0000-000000000002', 'credit', 75, 'libyana_balance', mp, NULL, 'admin', NULL);
  UPDATE manual_payments SET status = 'approved', ledger_id = e1.id, reviewed_at = now() WHERE id = mp;
  e2 := wallet_apply('bbbbbbbb-0000-0000-0000-000000000002', 'credit', 75, 'libyana_balance', mp, NULL, 'admin', NULL);
  ASSERT e1.id = e2.id, 'B12: double approval';
  BEGIN
    UPDATE manual_payments SET status = 'approved', ledger_id = NULL WHERE id = mp;
    RAISE EXCEPTION 'B12: approved without ledger';
  EXCEPTION WHEN check_violation THEN NULL; END;
  RAISE NOTICE 'PASS B12 manual payment approval is idempotent and requires a ledger entry';
END $$;

-- B13: الدفتر = الرصيد، وسجل التدقيق غير قابل للتعديل
DO $$
BEGIN
  ASSERT NOT EXISTS (
    SELECT 1 FROM wallets w
     WHERE w.balance_lyd <> (SELECT coalesce(sum(CASE direction WHEN 'credit' THEN amount_lyd ELSE -amount_lyd END), 0)
                             FROM wallet_ledger l WHERE l.tenant_id = w.tenant_id)), 'B13: ledger/balance mismatch';
  INSERT INTO admin_audit_log (action) VALUES ('test');
  BEGIN
    DELETE FROM admin_audit_log;
    RAISE EXCEPTION 'B13: audit log deletable';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  RAISE NOTICE 'PASS B13 sum(ledger) = balance for every wallet; audit log append-only';
END $$;

-- B14: تجديد بنفس Idempotency-Key مرتين => خصم واحد
DO $$
DECLARE p1 subscription_periods; p2 subscription_periods; bal numeric;
BEGIN
  PERFORM wallet_apply('aaaaaaaa-0000-0000-0000-000000000001', 'credit', 150, 'cash', NULL, NULL, 'admin', NULL);
  bal := (SELECT balance_lyd FROM wallets WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001');
  p1 := subscription_renew('aaaaaaaa-0000-0000-0000-000000000001', '11111111-0000-0000-0000-0000000000e1',
                           'admin', NULL, 'dddddddd-0000-0000-0000-000000000001');
  p2 := subscription_renew('aaaaaaaa-0000-0000-0000-000000000001', '11111111-0000-0000-0000-0000000000e1',
                           'admin', NULL, 'dddddddd-0000-0000-0000-000000000001');
  ASSERT p1.id = p2.id AND p1.period_end = p2.period_end, 'B14: second period created';
  ASSERT (SELECT balance_lyd FROM wallets WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') = bal - 150, 'B14: double debit';
  RAISE NOTICE 'PASS B14 renewal with the same idempotency key debits once';
END $$;

\echo 'ALL BILLING (ADMIN) TESTS PASSED'
