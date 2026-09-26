-- Live Inbox + الاستلام البشري + Leads CRM (المرحلة 6).
-- يُشغَّل بدور app_user. يختبر نصوص الاستعلامات الحقيقية من app/dashboard/queries.py و app/db/queries.py
-- (مولّدة كـ PREPARE عبر scripts/gen_prepared_sql.py ومُمرّرة بـ -v dash_sql=... -v db_sql=...).
-- المتطلبات (من run_sql_tests.sh بدور المالك): المستخدم 99..03 عضو agent في وكالة النور + صف staff_users له.
\set ON_ERROR_STOP on
\set QUIET on
\i :dash_sql
\i :db_sql

SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false),
       set_config('app.user_id',   '99999999-0000-0000-0000-000000000003', false) \g /dev/null

INSERT INTO contacts (id, tenant_id, channel, external_user_id, display_name, phone_e164) VALUES
  ('aaaaaaaa-0000-0000-0000-0000000000e9', 'aaaaaaaa-0000-0000-0000-000000000001', 'whatsapp',
   '218917778888', 'الحاج سالم', '+218917778888');
INSERT INTO conversations (id, tenant_id, contact_id, channel_account_id, last_inbound_at) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000099', 'aaaaaaaa-0000-0000-0000-000000000001',
   'aaaaaaaa-0000-0000-0000-0000000000e9', 'aaaaaaaa-0000-0000-0000-0000000000c1', now());

-- ---------------------------------------------------------------- N1: ملخص المحادثة + غير المقروء (trigger)
INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type, text_content) VALUES
  (app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-000000000099', 'whatsapp', 'inbound', 'customer', 'السلام عليكم');
INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type, text_content) VALUES
  (app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-000000000099', 'whatsapp', 'inbound', 'customer',
   'بكم عمرة رمضان للفرد؟');
DO $$ DECLARE c record; BEGIN
  SELECT * INTO c FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099';
  ASSERT c.unread_count = 2 AND c.last_message_direction = 'inbound'
     AND c.last_message_preview = 'بكم عمرة رمضان للفرد؟' AND c.last_message_at IS NOT NULL, 'N1: summary after inbound';
END $$;
INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type, msg_type) VALUES
  (app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-000000000099', 'whatsapp', 'outbound', 'bot', 'image');
DO $$ DECLARE c record; BEGIN
  SELECT * INTO c FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099';
  ASSERT c.unread_count = 2 AND c.last_message_direction = 'outbound' AND c.last_message_preview = '[image]',
         'N1: outbound does not add unread; media preview';
END $$;
EXECUTE mark_read('aaaaaaaa-0000-0000-0000-000000000099');
DO $$ BEGIN
  ASSERT (SELECT unread_count FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099') = 0, 'N1: mark_read';
  RAISE NOTICE 'PASS N1 conversation summary + unread maintained by trigger';
END $$;

-- ---------------------------------------------------------------- N2: قائمة الـ Inbox + الفلاتر + keyset + عزل الوكالات
CREATE TEMP TABLE n2_all    AS EXECUTE inbox_list('all', NULL, 'any', NULL, NULL, NULL, 50);
CREATE TEMP TABLE n2_search AS EXECUTE inbox_list('open', 'whatsapp', 'any', 'سالم', NULL, NULL, 50);
CREATE TEMP TABLE n2_phone  AS EXECUTE inbox_list('open', NULL, 'any', '7778888', NULL, NULL, 50);
CREATE TEMP TABLE n2_ig     AS EXECUTE inbox_list('open', 'instagram', 'any', NULL, NULL, NULL, 50);
CREATE TEMP TABLE n2_p1     AS EXECUTE inbox_list('all', NULL, 'any', NULL, NULL, NULL, 1);
SELECT last_message_at AS cur_ts, id AS cur_id FROM n2_p1 \gset
CREATE TEMP TABLE n2_p2     AS EXECUTE inbox_list('all', NULL, 'any', NULL, :'cur_ts', :'cur_id', 50);
DO $$ BEGIN
  ASSERT EXISTS (SELECT 1 FROM n2_all WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099'), 'N2: listed';
  ASSERT NOT EXISTS (SELECT 1 FROM n2_all WHERE id = 'bbbbbbbb-0000-0000-0000-000000000022'), 'N2: other tenant hidden';
  ASSERT (SELECT count(*) FROM n2_search) = 1 AND (SELECT count(*) FROM n2_phone) = 1, 'N2: search by name/phone';
  ASSERT (SELECT count(*) FROM n2_ig) = 0, 'N2: channel filter';
  ASSERT (SELECT count(*) FROM n2_p1) = 1
     AND NOT EXISTS (SELECT 1 FROM n2_p2 JOIN n2_p1 USING (id)), 'N2: keyset page 2 excludes page 1';
  ASSERT (SELECT count(*) FROM n2_p1) + (SELECT count(*) FROM n2_p2) = (SELECT count(*) FROM n2_all), 'N2: pages cover all';
  ASSERT (SELECT contact_name FROM n2_search) = 'الحاج سالم', 'N2: contact joined';
  RAISE NOTICE 'PASS N2 inbox list: filters, search, keyset pagination, tenant isolation';
END $$;

-- ---------------------------------------------------------------- N3: الاستلام يوقف البوت ويلغي ردوده المعلّقة فقط
UPDATE conversations SET reply_due_at = now() WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099';
EXECUTE enqueue_outbound('aaaaaaaa-0000-0000-0000-0000000000c1', '218917778888', 'reply',
                         'aaaaaaaa-0000-0000-0000-000000000099', NULL, 'text', '{"text":"رد البوت المعلّق"}') \g /dev/null
EXECUTE enqueue_outbound('aaaaaaaa-0000-0000-0000-0000000000c1', '+218911234567', 'staff_notification',
                         NULL, NULL, 'text', '{"text":"إشعار موظف"}') \g /dev/null
BEGIN;
EXECUTE lock_for_staff('aaaaaaaa-0000-0000-0000-000000000099') \g /dev/null
EXECUTE takeover('aaaaaaaa-0000-0000-0000-000000000099');
EXECUTE cancel_pending_bot_replies('aaaaaaaa-0000-0000-0000-000000000099');
COMMIT;
DO $$ DECLARE c record; BEGIN
  SELECT * INTO c FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099';
  ASSERT c.mode = 'human' AND c.takeover_by = '99999999-0000-0000-0000-000000000003' AND c.takeover_at IS NOT NULL
     AND c.reply_due_at IS NULL AND c.assigned_user_id = '99999999-0000-0000-0000-000000000003', 'N3: takeover state';
  ASSERT (SELECT status FROM outbound_messages WHERE conversation_id = c.id AND purpose = 'reply') = 'cancelled',
         'N3: pending bot reply cancelled';
  ASSERT (SELECT status FROM outbound_messages WHERE purpose = 'staff_notification'
           AND body->>'text' = 'إشعار موظف') = 'pending', 'N3: staff notification untouched';
  -- الـ worker لا يأخذ محادثة بشرية
  ASSERT NOT EXISTS (SELECT 1 FROM claim_due_conversations(50, interval '60 seconds')
                      WHERE conversation_id = c.id), 'N3: worker skips human conversation';
  RAISE NOTICE 'PASS N3 takeover: mode=human, bot replies cancelled, worker skips';
END $$;

-- ---------------------------------------------------------------- N4: رد الموظف + idempotency
EXECUTE insert_staff_message('aaaaaaaa-0000-0000-0000-000000000099', 'whatsapp',
        'هلا بيك يا حاج، عمرة رمضان بـ 9500 د.ل للفرد في الغرفة الرباعية', 'cccccccc-0000-0000-0000-000000000001') \gset staff_
EXECUTE enqueue_outbound('aaaaaaaa-0000-0000-0000-0000000000c1', '218917778888', 'staff_reply',
        'aaaaaaaa-0000-0000-0000-000000000099', :'staff_id', 'text', '{"text":"..."}') \gset ob_
EXECUTE mark_conversation_answered('aaaaaaaa-0000-0000-0000-000000000099');
-- متغيرات psql لا تُستبدل داخل $$ => تمريرها لكتل DO عبر إعدادات الجلسة
SELECT set_config('t.staff_id', :'staff_id', false), set_config('t.ob_id', :'ob_id', false) \g /dev/null
CREATE TEMP TABLE n4_dup AS EXECUTE message_by_client_id('cccccccc-0000-0000-0000-000000000001');
DO $$ BEGIN
  ASSERT (SELECT id FROM n4_dup) = current_setting('t.staff_id')::uuid, 'N4: duplicate lookup by client_msg_id';
  ASSERT (SELECT sender_type = 'staff' AND payload->>'user_id' = '99999999-0000-0000-0000-000000000003'
            FROM messages WHERE id = current_setting('t.staff_id')::uuid), 'N4: staff message attributed to user';
  ASSERT NOT EXISTS (SELECT 1 FROM messages WHERE conversation_id = 'aaaaaaaa-0000-0000-0000-000000000099'
                      AND direction = 'inbound' AND handled_at IS NULL), 'N4: inbound marked answered';
  BEGIN
    INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type, text_content, client_msg_id)
    VALUES (app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-000000000099', 'whatsapp', 'outbound', 'staff', 'x',
            'cccccccc-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'N4: duplicate client_msg_id accepted';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  RAISE NOTICE 'PASS N4 staff reply queued as staff_reply; client_msg_id idempotent';
END $$;

-- ---------------------------------------------------------------- N5: صفحات الرسائل + حالة التسليم
CREATE TEMP TABLE n5_p1 AS EXECUTE messages_page('aaaaaaaa-0000-0000-0000-000000000099', NULL, NULL, 2);
SELECT created_at AS m_ts, id AS m_id FROM n5_p1 ORDER BY created_at, id LIMIT 1 \gset
CREATE TEMP TABLE n5_p2 AS EXECUTE messages_page('aaaaaaaa-0000-0000-0000-000000000099', :'m_ts', :'m_id', 50);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM n5_p1) = 2 AND (SELECT count(*) FROM n5_p2) = 2, 'N5: 4 messages in 2 pages';
  ASSERT (SELECT delivery_status FROM n5_p1 WHERE id = current_setting('t.staff_id')::uuid) = 'pending', 'N5: delivery status joined';
  ASSERT NOT EXISTS (SELECT 1 FROM n5_p2 JOIN n5_p1 USING (id)), 'N5: no overlap';
  RAISE NOTICE 'PASS N5 message pages (keyset) with delivery status';
END $$;

-- ---------------------------------------------------------------- N6: إعادة إرسال رد فاشل فقط
EXECUTE retry_staff_message(:'staff_id') \g /dev/null
UPDATE outbound_messages SET status = 'failed', attempts = 5, last_error = 'timeout' WHERE id = :'ob_id';
EXECUTE retry_staff_message(:'staff_id') \g /dev/null
DO $$ BEGIN
  ASSERT (SELECT status = 'pending' AND attempts = 0 AND last_error IS NULL FROM outbound_messages
           WHERE id = current_setting('t.ob_id')::uuid), 'N6: failed -> pending';
  RAISE NOTICE 'PASS N6 retry only failed staff replies';
END $$;

-- ---------------------------------------------------------------- N7: الإسناد لعضو فقط
EXECUTE assign(NULL, 'aaaaaaaa-0000-0000-0000-000000000099') \g /dev/null
-- 99..01 ليس عضواً في وكالة النور
EXECUTE assign('99999999-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000099') \g /dev/null
DO $$ BEGIN
  ASSERT (SELECT assigned_user_id FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099') IS NULL,
         'N7: non-member not assigned';
END $$;
EXECUTE assign('99999999-0000-0000-0000-000000000003', 'aaaaaaaa-0000-0000-0000-000000000099') \g /dev/null
CREATE TEMP TABLE n7_me AS EXECUTE inbox_list('human', NULL, 'me', NULL, NULL, NULL, 50);
DO $$ BEGIN
  ASSERT (SELECT assigned_user_id FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099')
         = '99999999-0000-0000-0000-000000000003', 'N7: member assigned';
  ASSERT (SELECT count(*) FROM n7_me) = 1, 'N7: assigned=me filter';
  RAISE NOTICE 'PASS N7 assignment validated against active membership';
END $$;

-- ---------------------------------------------------------------- N8: الإرجاع للبوت
EXECUTE release('aaaaaaaa-0000-0000-0000-000000000099');
DO $$ BEGIN
  ASSERT (SELECT mode = 'bot' AND reply_due_at IS NULL AND takeover_by IS NULL FROM conversations
           WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099'), 'N8: release with nothing pending';
END $$;
EXECUTE takeover('aaaaaaaa-0000-0000-0000-000000000099');
INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type, text_content) VALUES
  (app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-000000000099', 'whatsapp', 'inbound', 'customer', 'وفيه تقسيط؟');
EXECUTE release('aaaaaaaa-0000-0000-0000-000000000099');
DO $$ BEGIN
  ASSERT (SELECT mode = 'bot' AND reply_due_at IS NOT NULL FROM conversations
           WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099'), 'N8: unanswered inbound => bot replies now';
  RAISE NOTICE 'PASS N8 release: bot resumes and answers pending customer messages';
END $$;

-- ---------------------------------------------------------------- N9: رد البوت المُهمل يُسجَّل + الإغلاق
EXECUTE insert_agent_run('aaaaaaaa-0000-0000-0000-000000000099', NULL, 'discarded', 'gpt-4o', 1, 10, 5, 900,
                         '[]', false, NULL, 'discarded: human took over during agent run');
EXECUTE close('aaaaaaaa-0000-0000-0000-000000000099');
DO $$ BEGIN
  ASSERT EXISTS (SELECT 1 FROM agent_runs WHERE status = 'discarded'), 'N9: discarded agent run';
  ASSERT (SELECT mode = 'closed' AND unread_count = 0 AND reply_due_at IS NULL FROM conversations
           WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099'), 'N9: closed';
  RAISE NOTICE 'PASS N9 discarded bot run recorded; close';
END $$;

-- ---------------------------------------------------------------- N10: Leads CRM
INSERT INTO leads (id, tenant_id, contact_id, conversation_id, package_id, full_name, phone_e164, adults, source_channel)
VALUES ('aaaaaaaa-0000-0000-0000-0000000000b9', app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-0000000000e9',
        'aaaaaaaa-0000-0000-0000-000000000099', 'aaaaaaaa-0000-0000-0000-0000000000a1', 'سالم الورفلي',
        '+218917778888', 3, 'whatsapp');
CREATE TEMP TABLE n10_new   AS EXECUTE leads_list('new', 'any', NULL, NULL, NULL, NULL, NULL, NULL, NULL, 50);
CREATE TEMP TABLE n10_q     AS EXECUTE leads_list(NULL, 'unassigned', NULL, 'aaaaaaaa-0000-0000-0000-0000000000a1',
                                                  '7778888', current_date, current_date, NULL, NULL, 50);
CREATE TEMP TABLE n10_past  AS EXECUTE leads_list(NULL, 'any', NULL, NULL, NULL, NULL, current_date - 1, NULL, NULL, 50);
CREATE TEMP TABLE n10_staff AS EXECUTE active_staff_exists('bbbbbbbb-0000-0000-0000-0000000000f2');
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM n10_new WHERE id = 'aaaaaaaa-0000-0000-0000-0000000000b9') = 1, 'N10: status filter';
  ASSERT NOT EXISTS (SELECT 1 FROM n10_new WHERE full_name = 'زبون الصفا'), 'N10: other tenant lead hidden';
  ASSERT (SELECT package_title IS NOT NULL FROM n10_q) AND (SELECT count(*) FROM n10_q) = 1, 'N10: combined filters';
  ASSERT (SELECT count(*) FROM n10_past) = 0, 'N10: to_date filter';
  ASSERT (SELECT count(*) FROM n10_staff) = 0, 'N10: other tenant staff invisible';
END $$;
BEGIN;
EXECUTE lead_for_update('aaaaaaaa-0000-0000-0000-0000000000b9') \g /dev/null
EXECUTE update_lead('contacted', NULL, 'aaaaaaaa-0000-0000-0000-0000000000f3', 'يبي يأكد بعد الراتب',
                    'aaaaaaaa-0000-0000-0000-0000000000b9');
EXECUTE insert_lead_event('aaaaaaaa-0000-0000-0000-0000000000b9', 'status_changed', '{"from":"new","to":"contacted"}');
COMMIT;
CREATE TEMP TABLE n10_ev AS EXECUTE lead_events('aaaaaaaa-0000-0000-0000-0000000000b9');
CREATE TEMP TABLE n10_as AS EXECUTE leads_list(NULL, 'staff', 'aaaaaaaa-0000-0000-0000-0000000000f3',
                                               NULL, NULL, NULL, NULL, NULL, NULL, 50);
CREATE TEMP TABLE n10_export AS EXECUTE leads_export(NULL, 'any', NULL, NULL, NULL, NULL, NULL);
DO $$ BEGIN
  ASSERT (SELECT status = 'contacted' AND first_contact_at IS NOT NULL FROM leads
           WHERE id = 'aaaaaaaa-0000-0000-0000-0000000000b9'), 'N10: first_contact_at set';
  ASSERT (SELECT actor_name FROM n10_ev WHERE event_type = 'status_changed') = 'موظف النور (حساب)', 'N10: actor staff';
  ASSERT (SELECT count(*) FROM n10_as) = 1 AND (SELECT assigned_name FROM n10_as) = 'موظف النور (حساب)',
         'N10: assigned filter';
  ASSERT (SELECT count(*) FROM n10_export) = (SELECT count(*) FROM leads), 'N10: export = all visible leads';
  BEGIN
    UPDATE leads SET status = 'lost' WHERE id = 'aaaaaaaa-0000-0000-0000-0000000000b9';
    RAISE EXCEPTION 'N10: lost without reason accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
  RAISE NOTICE 'PASS N10 leads CRM: filters, update, events with actor, export, tenant isolation';
END $$;

-- ---------------------------------------------------------------- N11: وكالة أخرى لا ترى ولا تغيّر
SELECT set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', false) \g /dev/null
CREATE TEMP TABLE n11_detail AS EXECUTE conversation_detail('aaaaaaaa-0000-0000-0000-000000000099');
CREATE TEMP TABLE n11_lead   AS EXECUTE lead_detail('aaaaaaaa-0000-0000-0000-0000000000b9');
CREATE TEMP TABLE n11_msgs   AS EXECUTE messages_page('aaaaaaaa-0000-0000-0000-000000000099', NULL, NULL, 50);
EXECUTE takeover('aaaaaaaa-0000-0000-0000-000000000099');
EXECUTE update_lead('spam', NULL, NULL, NULL, 'aaaaaaaa-0000-0000-0000-0000000000b9');
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false) \g /dev/null
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM n11_detail) = 0 AND (SELECT count(*) FROM n11_lead) = 0
     AND (SELECT count(*) FROM n11_msgs) = 0, 'N11: cross-tenant read';
  ASSERT (SELECT mode FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099') = 'closed',
         'N11: cross-tenant takeover had no effect';
  ASSERT (SELECT status FROM leads WHERE id = 'aaaaaaaa-0000-0000-0000-0000000000b9') = 'contacted',
         'N11: cross-tenant lead update had no effect';
  RAISE NOTICE 'PASS N11 RLS: other tenant cannot read or change inbox/leads';
END $$;

\echo 'ALL INBOX/LEADS (APP_USER) TESTS PASSED'
