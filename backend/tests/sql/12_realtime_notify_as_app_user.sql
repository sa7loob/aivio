-- NOTIFY tenant_events: يُشغَّل بدور app_user بعد 11، والمخرجات تُفحص في run_sql_tests.sh.
-- psql يطبع "Asynchronous notification ... payload ..." بعد كل أمر يُنفَّذ (بعد الـ commit).
\set ON_ERROR_STOP on
\set QUIET on
LISTEN tenant_events;
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false) \g /dev/null
INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type, text_content) VALUES
  (app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-000000000099', 'whatsapp', 'inbound', 'customer', 'NOTIFY-TEST');
-- تحديث حقل worker فقط (reply_lease_until) => لا إشعار
UPDATE conversations SET reply_lease_until = now() WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099';
UPDATE conversations SET reply_lease_until = NULL  WHERE id = 'aaaaaaaa-0000-0000-0000-000000000099';
UPDATE leads SET notes = 'NOTIFY-TEST' WHERE id = 'aaaaaaaa-0000-0000-0000-0000000000b9';
UPDATE outbound_messages SET status = 'sent' WHERE purpose = 'staff_reply'
   AND conversation_id = 'aaaaaaaa-0000-0000-0000-000000000099';
SELECT 1 \g /dev/null
