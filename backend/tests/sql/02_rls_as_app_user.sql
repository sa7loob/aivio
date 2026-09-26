-- اختبارات العزل (RLS) — يُشغَّل بدور app_user (دور التشغيل الحقيقي).
-- كل DO block هو transaction مستقلة؛ set_config(..., true) = SET LOCAL.
\set ON_ERROR_STOP on
\set QUIET on

-- T1: بدون ضبط الوكالة => لا شيء مرئي (Fail-closed)
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM tenants)  = 0, 'T1: tenants visible without tenant context';
  ASSERT (SELECT count(*) FROM packages) = 0, 'T1: packages visible without tenant context';
  ASSERT (SELECT count(*) FROM leads)    = 0, 'T1: leads visible without tenant context';
  RAISE NOTICE 'PASS T1 fail-closed without tenant context';
END $$;

-- T2: وكالة النور ترى بياناتها فقط
DO $$
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  ASSERT (SELECT count(*) FROM tenants) = 1, 'T2: should see exactly own tenant';
  ASSERT (SELECT count(*) FROM packages) = 4, 'T2: should see 4 own packages';
  ASSERT NOT EXISTS (SELECT 1 FROM packages WHERE tenant_id <> app_current_tenant_id()), 'T2: foreign package leaked';
  ASSERT (SELECT count(*) FROM package_prices) = 5, 'T2: prices count';
  ASSERT (SELECT count(*) FROM leads) = 0, 'T2: must not see other agency lead';
  RAISE NOTICE 'PASS T2 tenant sees only own rows';
END $$;

-- T3: الكتابة بـ tenant_id لوكالة أخرى تُرفض (WITH CHECK)
DO $$
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  BEGIN
    INSERT INTO packages (tenant_id, kind, title)
    VALUES ('bbbbbbbb-0000-0000-0000-000000000002', 'umrah', 'حقن في وكالة أخرى');
    RAISE EXCEPTION 'T3: cross-tenant insert was allowed';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS T3 cross-tenant insert rejected (%)', SQLERRM;
  END;
END $$;

-- T4: تعديل/حذف صفوف وكالة أخرى لا يؤثر على شيء
DO $$
DECLARE n int;
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  UPDATE packages SET title = 'x' WHERE id = 'bbbbbbbb-0000-0000-0000-0000000000b1';
  GET DIAGNOSTICS n = ROW_COUNT;
  ASSERT n = 0, 'T4: updated foreign row';
  DELETE FROM leads;
  GET DIAGNOSTICS n = ROW_COUNT;
  ASSERT n = 0, 'T4: deleted foreign lead';
  RAISE NOTICE 'PASS T4 cannot update/delete foreign rows';
END $$;

-- T5: المفاتيح المركّبة تمنع ربط Lead ببرنامج وكالة أخرى (حتى مع tenant_id صحيح)
DO $$
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  BEGIN
    INSERT INTO leads (tenant_id, contact_id, package_id, full_name, phone_e164, source_channel)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000e1',
            'bbbbbbbb-0000-0000-0000-0000000000b1', 'اختبار', '+218913334444', 'whatsapp');
    RAISE EXCEPTION 'T5: lead linked to foreign package';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T5 composite FK blocks cross-tenant reference';
  END;
  -- موعد لا يتبع البرنامج المحدد
  BEGIN
    INSERT INTO package_prices (tenant_id, package_id, departure_id, room_type, amount)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1',
            'aaaaaaaa-0000-0000-0000-0000000000d2', 'single', 15000);
    RAISE EXCEPTION 'T5b: price linked to departure of another package';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS T5b departure must belong to the same package';
  END;
END $$;

-- T6: app_user لا يستطيع إنشاء أو حذف وكالة
DO $$
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  BEGIN
    DELETE FROM tenants;
    RAISE EXCEPTION 'T6: app_user deleted tenant';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS T6 app_user cannot delete tenants';
  END;
END $$;

-- T7: القيمة لا تتسرب بعد انتهاء الـ transaction ('' => NULL)
BEGIN;
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true) \g /dev/null
COMMIT;
DO $$
BEGIN
  ASSERT current_setting('app.tenant_id', true) IS NOT DISTINCT FROM ''
         OR current_setting('app.tenant_id', true) IS NULL, 'T7: unexpected setting';
  ASSERT app_current_tenant_id() IS NULL, 'T7: tenant context leaked';
  ASSERT (SELECT count(*) FROM packages) = 0, 'T7: rows visible after commit';
  RAISE NOTICE 'PASS T7 tenant context does not leak across transactions';
END $$;

-- T8: دوال الـ worker تعمل بدون سياق وكالة وتعيد معرّفات فقط
DO $$
DECLARE r record;
BEGIN
  SELECT * INTO r FROM resolve_channel_account('whatsapp', 'PNID-NOOR-TEST');
  ASSERT r.tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001', 'T8: resolve failed';
  ASSERT NOT EXISTS (SELECT 1 FROM resolve_channel_account('whatsapp', 'UNKNOWN')), 'T8: unknown resolved';
  -- محادثة النور مستحقة، ومحادثة الصفا بعد ساعة
  ASSERT (SELECT count(*) FROM claim_due_conversations(10)) = 1, 'T8: claim due count';
  -- الحجز (lease) يمنع أخذها مرة ثانية
  ASSERT (SELECT count(*) FROM claim_due_conversations(10)) = 0, 'T8: lease not respected';
  RAISE NOTICE 'PASS T8 resolve/claim functions (cross-tenant ids only)';
END $$;

-- T9: الـ View يحترم RLS (security_invoker)
DO $$
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  ASSERT NOT EXISTS (SELECT 1 FROM v_package_offers WHERE tenant_id <> app_current_tenant_id()), 'T9: view leaked';
  ASSERT (SELECT price_from FROM v_package_offers
          WHERE package_id = 'aaaaaaaa-0000-0000-0000-0000000000a1') = 9500, 'T9: price_from';
  ASSERT (SELECT price_from FROM v_package_offers
          WHERE package_id = 'aaaaaaaa-0000-0000-0000-0000000000a2') = 6500, 'T9: departure price';
  ASSERT NOT EXISTS (SELECT 1 FROM v_package_offers WHERE title = 'رحلة إسطنبول'), 'T9: draft visible';
  RAISE NOTICE 'PASS T9 v_package_offers respects RLS and pricing';
END $$;

-- T10: Idempotency — نفس رسالة Meta مرتين = صف واحد
DO $$
DECLARE n int;
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  FOR i IN 1..2 LOOP
    INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type,
                          external_message_id, text_content)
    VALUES (app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-000000000011', 'whatsapp',
            'inbound', 'customer', 'wamid.TEST123', 'قداش العمرة؟')
    ON CONFLICT (tenant_id, channel, external_message_id) DO NOTHING;
  END LOOP;
  SELECT count(*) INTO n FROM messages WHERE external_message_id = 'wamid.TEST123';
  ASSERT n = 1, 'T10: duplicate message stored';
  RAISE NOTICE 'PASS T10 duplicate webhook delivery stored once';
END $$;

-- T11: Lead مفتوح واحد لكل زبون/برنامج => UPSERT
DO $$
DECLARE n int;
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  FOR i IN 1..2 LOOP
    INSERT INTO leads (tenant_id, contact_id, package_id, full_name, phone_e164, adults, source_channel)
    VALUES (app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-0000000000e1',
            'aaaaaaaa-0000-0000-0000-0000000000a1', 'أبو محمد', '+218913334444', 1 + i, 'whatsapp')
    ON CONFLICT (tenant_id, contact_id, package_id) WHERE status IN ('new','contacted','qualified')
    DO UPDATE SET adults = EXCLUDED.adults;
  END LOOP;
  SELECT count(*) INTO n FROM leads;
  ASSERT n = 1, 'T11: duplicate open lead';
  ASSERT (SELECT adults FROM leads) = 3, 'T11: upsert did not update';
  RAISE NOTICE 'PASS T11 one open lead per contact/package (upsert)';
END $$;

\echo 'ALL RLS TESTS PASSED'
