-- وكالة موقوفة: claim_ai_jobs لا يأخذ مهامها (لا إنفاق على الذكاء الاصطناعي)، وتُؤخذ بعد إعادة التفعيل.
-- يُشغَّل بدور المالك بعد 13 (يحتاج تغيير حالة الاشتراك، وهو ممنوع على app_user).
\set ON_ERROR_STOP on
\set QUIET on

INSERT INTO messages (id, tenant_id, conversation_id, channel, direction, sender_type, msg_type, payload) VALUES
  ('bbbbbbbb-0000-0000-0000-0000000007b1', 'bbbbbbbb-0000-0000-0000-000000000002',
   'bbbbbbbb-0000-0000-0000-000000000022', 'messenger', 'inbound', 'customer', 'audio', '{}');
INSERT INTO ai_jobs (tenant_id, kind, message_id) VALUES
  ('bbbbbbbb-0000-0000-0000-000000000002', 'transcribe', 'bbbbbbbb-0000-0000-0000-0000000007b1');
-- مهام النور من 13 (الاستخراج والـ embedding) ما زالت معلّقة
UPDATE ai_jobs SET next_attempt_at = now(), locked_until = NULL WHERE status IN ('pending','processing');

UPDATE subscriptions SET status = 'suspended' WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001';
CREATE TEMP TABLE s1_suspended AS
  SELECT * FROM claim_ai_jobs(ARRAY['transcribe','catalog_extract','embed_knowledge'], 50, '1 minute');
UPDATE subscriptions SET status = 'active' WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001';
CREATE TEMP TABLE s1_active AS
  SELECT * FROM claim_ai_jobs(ARRAY['transcribe','catalog_extract','embed_knowledge'], 50, '1 minute');

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM s1_suspended WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') = 0,
         'S1: suspended tenant jobs not claimed';
  ASSERT (SELECT count(*) FROM s1_suspended WHERE tenant_id = 'bbbbbbbb-0000-0000-0000-000000000002') = 1,
         'S1: other tenants unaffected';
  ASSERT (SELECT count(*) FROM s1_active WHERE tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001') >= 2,
         'S1: claimed after reactivation';
  RAISE NOTICE 'PASS S1 suspended tenant: AI jobs wait (no spend), resume after reactivation';
END $$;
