-- تفريغ صوتي يحدّث نص رسالة => حدث 'message' للوحة (معرّفات فقط). تحديث حقل آخر => لا حدث.
-- يُشغَّل بدور app_user بعد 13، والمخرجات تُفحص في run_sql_tests.sh.
\set ON_ERROR_STOP on
\set QUIET on
LISTEN tenant_events;
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false) \g /dev/null
UPDATE messages SET handled_at = now() WHERE id = 'aaaaaaaa-0000-0000-0000-0000000007a1';
UPDATE messages SET text_content = 'TRANSCRIPT-NOTIFY-TEST' WHERE id = 'aaaaaaaa-0000-0000-0000-0000000007a1';
SELECT 1 \g /dev/null
