-- الهوية والتسجيل الذاتي والربط — يُشغَّل بدور app_user بعد اختبارات الفوترة (الخطة basic موجودة).
\set ON_ERROR_STOP on
\set QUIET on

INSERT INTO users (id, email, full_name, phone_e164, password_hash) VALUES
  ('99999999-0000-0000-0000-000000000001', 'owner@rayan.ly', 'صاحب الريان', '+218911112222', 'x'),
  ('99999999-0000-0000-0000-000000000002', 'agent@rayan.ly', 'موظف الريان', '+218921112222', 'x'),
  ('99999999-0000-0000-0000-000000000003', 'other@noor.ly',  'شخص آخر',     NULL,            'x');

-- I1: التسجيل الذاتي ينشئ الوكالة والعضوية والموظف والاشتراك التجريبي
DO $$
DECLARE t uuid; r record;
BEGIN
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000001', true);
  t := register_tenant('rayan-self', 'وكالة الريان', 'travel_hajj_umrah', 'basic', 14, NULL);
  SELECT * INTO r FROM my_tenants();
  ASSERT r.tenant_id = t AND r.role = 'owner' AND r.subscription_status = 'trialing', 'I1: my_tenants';
  PERFORM set_config('app.tenant_id', t::text, true);
  ASSERT (SELECT notify_on_new_lead AND whatsapp_phone = '+218911112222'
            FROM staff_users WHERE user_id = '99999999-0000-0000-0000-000000000001'), 'I1: owner staff row';
  RAISE NOTICE 'PASS I1 self-registration creates tenant, owner membership, staff, trial';
END $$;

-- I2: بدون مستخدم لا تسجيل، وبخطة غير موجودة يُرفض
DO $$
BEGIN
  BEGIN
    PERFORM register_tenant('no-user', 'x', 'retail', 'basic', 14, NULL);
    RAISE EXCEPTION 'I2: registered without user context';
  EXCEPTION WHEN SQLSTATE 'AU001' THEN NULL; END;
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000003', true);
  BEGIN
    PERFORM register_tenant('bad-plan', 'x', 'retail', 'no-such-plan', 14, NULL);
    RAISE EXCEPTION 'I2: unknown plan accepted';
  EXCEPTION WHEN SQLSTATE 'BL005' THEN NULL; END;
  BEGIN
    PERFORM register_tenant('rayan-self', 'x', 'retail', 'basic', 14, NULL);
    RAISE EXCEPTION 'I2: duplicate slug accepted';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  RAISE NOTICE 'PASS I2 registration requires user context, a real plan, a unique slug';
END $$;

-- I3: لا إضافة عضويات مباشرة، والمستخدم لا يرى إلا عضوياته + وكالته الحالية
DO $$
DECLARE rayan uuid := (SELECT tenant_id FROM memberships
                        WHERE user_id = '99999999-0000-0000-0000-000000000001' LIMIT 1);
BEGIN
  -- الرؤية عبر app.user_id وحده (قبل اختيار وكالة)
  ASSERT rayan IS NULL, 'I3: memberships visible without any context';
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000003', true);
  ASSERT (SELECT count(*) FROM memberships) = 0, 'I3: other user sees memberships';
  BEGIN
    INSERT INTO memberships (tenant_id, user_id, role)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', '99999999-0000-0000-0000-000000000003', 'owner');
    RAISE EXCEPTION 'I3: direct membership insert allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  RAISE NOTICE 'PASS I3 memberships: no direct inserts, visible only to self / current tenant';
END $$;

-- I4: الدعوات
DO $$
DECLARE t uuid; r record;
BEGIN
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000001', true);
  SELECT tenant_id INTO t FROM my_tenants() LIMIT 1;
  PERFORM set_config('app.tenant_id', t::text, true);
  INSERT INTO invitations (tenant_id, role, token_hash, invited_by, expires_at) VALUES
    (t, 'agent', 'inv-ok',   '99999999-0000-0000-0000-000000000001', now() + interval '7 days'),
    (t, 'agent', 'inv-old',  '99999999-0000-0000-0000-000000000001', now() - interval '1 minute');

  PERFORM set_config('app.tenant_id', '', true);
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000002', true);
  SELECT * INTO r FROM accept_invitation('inv-ok');
  ASSERT r.tenant_id = t AND r.role = 'agent', 'I4: accept';
  SELECT * INTO r FROM accept_invitation('inv-ok');           -- نفس المستخدم مرة ثانية: idempotent
  ASSERT (SELECT count(*) FROM my_tenants()) = 1, 'I4: my_tenants for agent';
  BEGIN
    PERFORM accept_invitation('inv-old');
    RAISE EXCEPTION 'I4: expired invitation accepted';
  EXCEPTION WHEN SQLSTATE 'AU002' THEN NULL; END;
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000003', true);
  BEGIN
    PERFORM accept_invitation('inv-ok');
    RAISE EXCEPTION 'I4: used invitation accepted by someone else';
  EXCEPTION WHEN SQLSTATE 'AU002' THEN NULL; END;
  RAISE NOTICE 'PASS I4 invitations: accept once per user, expired/used rejected';
END $$;

-- I5: حجز القناة لا يسرق ولا يكشف قناة وكالة أخرى
DO $$
DECLARE t uuid; r record; n int;
BEGIN
  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000001', true);
  SELECT tenant_id INTO t FROM my_tenants() LIMIT 1;
  PERFORM set_config('app.tenant_id', t::text, true);
  SELECT * INTO r FROM upsert_channel_account('whatsapp', '777000777', '555', 'Rayan', '\\x01', false, '{"onboarding":"self_serve"}');
  ASSERT r.outcome = 'created', 'I5: created';
  SELECT * INTO r FROM upsert_channel_account('whatsapp', '777000777', NULL, NULL, '\\x02', false, '{}');
  ASSERT r.outcome = 'updated', 'I5: updated';

  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  SELECT * INTO r FROM upsert_channel_account('whatsapp', '777000777', NULL, 'steal', '\\x03', false, '{}');
  ASSERT r.outcome = 'conflict' AND r.channel_account_id IS NULL, 'I5: other tenant got the channel';
  SELECT count(*) INTO n FROM channel_accounts WHERE external_id = '777000777';
  ASSERT n = 0, 'I5: foreign channel visible';
  RAISE NOTICE 'PASS I5 upsert_channel_account: create/update own, conflict without leak';
END $$;

-- I6: مراقبة الصحة تحجز القنوات مرة واحدة لكل فترة
DO $$
BEGIN
  -- قنوات بيانات الاختبار لم تُفحص أبداً => مستحقة؛ الاستدعاء الثاني فوراً => لا شيء (lease)
  ASSERT (SELECT count(*) FROM channels_due_for_health_check(100, '6 hours')) > 0, 'I6: nothing due';
  ASSERT (SELECT count(*) FROM channels_due_for_health_check(100, '6 hours')) = 0, 'I6: lease ignored';
  RAISE NOTICE 'PASS I6 health-check lease respects last_checked_at';
END $$;

-- I7: دعوة مالك لمكتب سُجّل يدوياً (من لوحة الإدارة) تربط الموظف الموجود بدل تكراره
DO $$
DECLARE r record;
BEGIN
  -- تجهيز: موظف مالك قديم لوكالة الصفا بنفس رقم المستخدم 3 (لا يوجد له user_id)
  PERFORM set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', true);
  UPDATE staff_users SET whatsapp_phone = '+218931231231' WHERE tenant_id = app_current_tenant_id();
  INSERT INTO invitations (tenant_id, role, token_hash, expires_at)
  VALUES ('bbbbbbbb-0000-0000-0000-000000000002', 'owner', 'inv-owner', now() + interval '7 days');
  PERFORM set_config('app.tenant_id', '', true);
  UPDATE users SET phone_e164 = '+218931231231' WHERE id = '99999999-0000-0000-0000-000000000003';

  PERFORM set_config('app.user_id', '99999999-0000-0000-0000-000000000003', true);
  SELECT * INTO r FROM accept_invitation('inv-owner');
  ASSERT r.role = 'owner', 'I7: owner role';
  PERFORM set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', true);
  ASSERT (SELECT count(*) FROM staff_users) = 1, 'I7: duplicate staff row created';
  ASSERT (SELECT user_id FROM staff_users) = '99999999-0000-0000-0000-000000000003', 'I7: staff not linked';
  RAISE NOTICE 'PASS I7 owner invitation links an admin-registered office to a login account';
END $$;

\echo 'ALL IDENTITY TESTS PASSED'
