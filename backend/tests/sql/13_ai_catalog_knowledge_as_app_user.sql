-- المرحلة 7a: طابور ai_jobs + التفريغ الصوتي + استيراد الكتالوج + معلومات الموظفين.
-- يُشغَّل بدور app_user على نصوص الاستعلامات الحقيقية من app/ai/queries.py (و TOOL_KNOWLEDGE_FTS من
-- app/db/queries.py) مولّدة كـ PREPARE: -v ai_sql=... -v kb_sql=...
-- المتطلبات: 01 (وكالتا النور A والصفا B) + المستخدم 99..03 عضو agent في النور (من run_sql_tests.sh).
\set ON_ERROR_STOP on
\set QUIET on
\i :ai_sql
\i :kb_sql

-- تأكيد بقيم psql (\gset): المتغيرات لا تُستبدل داخل DO $$ ... $$
CREATE FUNCTION pg_temp.check(ok boolean, msg text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN IF ok IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', msg; END IF; END $$;

\set MSG 'aaaaaaaa-0000-0000-0000-0000000007a1'
\set CONV 'aaaaaaaa-0000-0000-0000-000000000701'

SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false),
       set_config('app.user_id',   '99999999-0000-0000-0000-000000000003', false) \g /dev/null

INSERT INTO contacts (id, tenant_id, channel, external_user_id, display_name) VALUES
  ('aaaaaaaa-0000-0000-0000-0000000007c1', app_current_tenant_id(), 'whatsapp', '218915550001', 'زبون صوتي');
INSERT INTO conversations (id, tenant_id, contact_id, channel_account_id, last_inbound_at) VALUES
  (:'CONV', app_current_tenant_id(), 'aaaaaaaa-0000-0000-0000-0000000007c1',
   'aaaaaaaa-0000-0000-0000-0000000000c1', now());
INSERT INTO messages (id, tenant_id, conversation_id, channel, direction, sender_type, msg_type, payload) VALUES
  (:'MSG', app_current_tenant_id(), :'CONV', 'whatsapp', 'inbound', 'customer', 'audio',
   '{"audio": {"id": "MEDIA-1", "mime_type": "audio/ogg"}}');

-- ---------------------------------------------------------------- Q1: مهمة واحدة لكل رسالة + عزل الوكالات
EXECUTE enqueue_transcription(:'MSG');
EXECUTE enqueue_transcription(:'MSG');
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM ai_jobs WHERE message_id = 'aaaaaaaa-0000-0000-0000-0000000007a1') = 1,
         'Q1: one transcription job per message (idempotent)';
  ASSERT (SELECT status FROM ai_jobs WHERE message_id = 'aaaaaaaa-0000-0000-0000-0000000007a1') = 'pending', 'Q1: pending';
  BEGIN
    INSERT INTO ai_jobs (tenant_id, kind, catalog_import_id, message_id)
    VALUES (app_current_tenant_id(), 'transcribe', NULL, 'aaaaaaaa-0000-0000-0000-0000000007a1');
    RAISE EXCEPTION 'Q1: duplicate transcription job accepted';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO ai_jobs (tenant_id, kind, message_id)
    VALUES (app_current_tenant_id(), 'catalog_extract', 'aaaaaaaa-0000-0000-0000-0000000007a1');
    RAISE EXCEPTION 'Q1: kind/reference mismatch accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;
SELECT set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', false) \g /dev/null
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM ai_jobs) = 0, 'Q1: other tenant sees no jobs';
  BEGIN   -- مرجع لرسالة وكالة أخرى: المفتاح المركّب يمنعه
    INSERT INTO ai_jobs (tenant_id, kind, message_id)
    VALUES (app_current_tenant_id(), 'transcribe', 'aaaaaaaa-0000-0000-0000-0000000007a1');
    RAISE EXCEPTION 'Q1: cross-tenant message reference accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  BEGIN   -- صف لوكالة أخرى: RLS WITH CHECK
    INSERT INTO ai_jobs (tenant_id, kind, message_id)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'transcribe', 'aaaaaaaa-0000-0000-0000-0000000007a1');
    RAISE EXCEPTION 'Q1: insert for another tenant accepted';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  RAISE NOTICE 'PASS Q1 one job per message, kind/ref check, composite FK + RLS isolation';
END $$;

-- ---------------------------------------------------------------- Q2: claim_ai_jobs (بدون سياق وكالة) + lease + فلتر النوع
SELECT set_config('app.tenant_id', '', false) \g /dev/null
CREATE TEMP TABLE q2_other AS EXECUTE claim_ai_jobs(ARRAY['catalog_extract'], 10, 60);
CREATE TEMP TABLE q2_claim AS EXECUTE claim_ai_jobs(ARRAY['transcribe'], 10, 60);
CREATE TEMP TABLE q2_again AS EXECUTE claim_ai_jobs(ARRAY['transcribe'], 10, 60);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM q2_other) = 0, 'Q2: kinds filter';
  ASSERT (SELECT count(*) FROM q2_claim) = 1, 'Q2: claimed';
  ASSERT (SELECT kind FROM q2_claim) = 'transcribe'
     AND (SELECT tenant_id FROM q2_claim) = 'aaaaaaaa-0000-0000-0000-000000000001', 'Q2: ids + tenant + kind only';
  ASSERT (SELECT count(*) FROM q2_again) = 0, 'Q2: lease blocks a second worker';
  ASSERT (SELECT count(*) FROM ai_jobs) = 0, 'Q2: no tenant context => table itself invisible (fail-closed)';
END $$;
SELECT job_id AS job1 FROM q2_claim \gset
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false) \g /dev/null
CREATE TEMP TABLE q2_loaded AS EXECUTE load_ai_job(:'job1');
DO $$ BEGIN
  ASSERT (SELECT attempts FROM q2_loaded) = 1 AND (SELECT kind FROM q2_loaded) = 'transcribe', 'Q2: processing, attempt 1';
END $$;
EXECUTE retry_ai_job('network error', 0, :'job1');
SELECT set_config('app.tenant_id', '', false) \g /dev/null
CREATE TEMP TABLE q2_retry AS EXECUTE claim_ai_jobs(ARRAY['transcribe'], 10, 60);
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false) \g /dev/null
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM q2_retry) = 1, 'Q2: retried job claimable after its delay';
  ASSERT (SELECT attempts FROM ai_jobs WHERE message_id = 'aaaaaaaa-0000-0000-0000-0000000007a1') = 2, 'Q2: attempts';
  RAISE NOTICE 'PASS Q2 claim_ai_jobs: kinds filter, lease, retry, ids only, fail-closed without tenant';
END $$;

-- ---------------------------------------------------------------- V1: الرد ينتظر التفريغ، والنص يُكتب مرة واحدة
CREATE TEMP TABLE v1_wait AS EXECUTE pending_transcriptions(ARRAY[:'MSG']::uuid[]);
SELECT created_at AS msg_created FROM messages WHERE id = :'MSG' \gset
EXECUTE set_message_transcript('قداش عمرة رمضان؟', :'MSG') \g /dev/null
EXECUTE set_message_transcript('نص ثاني لا يجب أن يُكتب', :'MSG') \g /dev/null
CREATE TEMP TABLE v1_after AS EXECUTE pending_transcriptions(ARRAY[:'MSG']::uuid[]);
EXECUTE preview_transcript('🎤 قداش عمرة رمضان؟', :'CONV', :'msg_created');
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM v1_wait) = 1, 'V1: unfinished transcription blocks the reply';
  ASSERT (SELECT text_content FROM messages WHERE id = 'aaaaaaaa-0000-0000-0000-0000000007a1') = 'قداش عمرة رمضان؟',
         'V1: transcript written once (retry does not overwrite)';
  ASSERT (SELECT count(*) FROM v1_after) = 0, 'V1: text present => reply no longer waits (job may still be processing)';
  ASSERT (SELECT last_message_preview FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000701')
         = '🎤 قداش عمرة رمضان؟', 'V1: list preview shows the transcript';
END $$;
INSERT INTO messages (tenant_id, conversation_id, channel, direction, sender_type, text_content) VALUES
  (app_current_tenant_id(), :'CONV', 'whatsapp', 'outbound', 'bot', 'رد البوت');
EXECUTE preview_transcript('🎤 تحديث متأخر', :'CONV', :'msg_created');
UPDATE conversations SET reply_due_at = '2030-01-01 00:00:00+00' WHERE id = :'CONV';
EXECUTE defer_reply(2, :'CONV', '2029-01-01 00:00:00+00');
DO $$ DECLARE c record; BEGIN
  ASSERT (SELECT last_message_preview FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000701') = 'رد البوت',
         'V1: late transcript does not overwrite a newer preview';
  SELECT * INTO c FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000701';
  ASSERT c.reply_due_at = '2030-01-01 00:00:00+00', 'V1: defer ignored when due time changed (newer message)';
END $$;
UPDATE conversations SET reply_lease_until = now() + interval '1 minute' WHERE id = :'CONV';
EXECUTE defer_reply(2, :'CONV', '2030-01-01 00:00:00+00');
EXECUTE finish_ai_job('done', NULL, 'gpt-4o-transcribe', 50, 12, 81, :'job1');
DO $$ DECLARE c record; j record; BEGIN
  SELECT * INTO c FROM conversations WHERE id = 'aaaaaaaa-0000-0000-0000-000000000701';
  ASSERT c.reply_due_at BETWEEN now() AND now() + interval '5 seconds' AND c.reply_lease_until IS NULL,
         'V1: defer moves the reply a few seconds and releases the lease';
  SELECT * INTO j FROM ai_jobs WHERE message_id = 'aaaaaaaa-0000-0000-0000-0000000007a1';
  ASSERT j.status = 'done' AND j.locked_until IS NULL AND j.finished_at IS NOT NULL
     AND j.model = 'gpt-4o-transcribe' AND j.input_tokens = 50 AND j.duration_ms = 81, 'V1: finish records cost + latency';
  RAISE NOTICE 'PASS V1 reply waits for transcription; transcript written once; preview; defer; job finish';
END $$;

-- ---------------------------------------------------------------- V2: كلمات الوكالة للتفريغ (RLS + بدون المؤرشف)
INSERT INTO packages (tenant_id, kind, title, status) VALUES
  (app_current_tenant_id(), 'umrah', 'برنامج مؤرشف قديم', 'archived');
CREATE TEMP TABLE v2_terms AS EXECUTE tenant_vocabulary;
DO $$ BEGIN
  ASSERT EXISTS (SELECT 1 FROM v2_terms WHERE term = 'فندق المثال مكة'), 'V2: own hotel names';
  ASSERT EXISTS (SELECT 1 FROM v2_terms WHERE term = 'العشر الأواخر'), 'V2: own aliases';
  ASSERT NOT EXISTS (SELECT 1 FROM v2_terms WHERE term = 'عمرة رمضان الاقتصادية'), 'V2: other tenant hidden';
  ASSERT NOT EXISTS (SELECT 1 FROM v2_terms WHERE term = 'برنامج مؤرشف قديم'), 'V2: archived excluded';
  RAISE NOTICE 'PASS V2 transcription vocabulary: own titles/aliases/hotels only, archived excluded';
END $$;

-- ---------------------------------------------------------------- C1: رفع البروشور + منع التكرار + عزل
\set SHA '''aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'''
EXECUTE insert_catalog_import('brochure.png', 'image/png', 4, :SHA, '\x89504e47'::bytea) \g /dev/null
SELECT id AS imp FROM catalog_imports WHERE sha256 = :SHA \gset
EXECUTE enqueue_catalog_extraction(:'imp');
EXECUTE enqueue_catalog_extraction(:'imp');
CREATE TEMP TABLE c1_dup AS EXECUTE find_import_by_sha(:SHA);
DO $$ DECLARE i record; BEGIN
  SELECT * INTO i FROM catalog_imports WHERE sha256 = repeat('a', 64);
  ASSERT i.status = 'pending' AND i.created_by = '99999999-0000-0000-0000-000000000003' AND i.file_data IS NOT NULL,
         'C1: import stored with its uploader';
  ASSERT (SELECT count(*) FROM ai_jobs WHERE catalog_import_id = i.id) = 1, 'C1: one extraction job per import';
  ASSERT (SELECT count(*) FROM c1_dup) = 1, 'C1: same file found by sha256 (no second extraction)';
  BEGIN
    UPDATE catalog_imports SET file_data = NULL WHERE id = i.id;
    RAISE EXCEPTION 'C1: pending import without file accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;
SELECT set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', false) \g /dev/null
CREATE TEMP TABLE c1_b AS EXECUTE find_import_by_sha(:SHA);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM catalog_imports) = 0 AND (SELECT count(*) FROM c1_b) = 0, 'C1: other tenant sees no imports';
  RAISE NOTICE 'PASS C1 brochure import: uploader recorded, dedupe by sha256, file kept until processed, isolation';
END $$;
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false) \g /dev/null

-- ---------------------------------------------------------------- C2: المسودات مخفية عن البوت حتى الاعتماد
EXECUTE insert_draft_package('umrah', 'عمرة الربيع المستوردة', 'الربيع', 'وصف', 12, 8, 3, 'طرابلس', NULL,
                             '["التأشيرة"]', '[]', NULL, 'عربون 500', :'imp') \g /dev/null
SELECT id AS pkg FROM packages WHERE title = 'عمرة الربيع المستوردة' \gset
EXECUTE insert_package_alias(:'pkg', 'عمرة الربيع');
EXECUTE insert_package_alias(:'pkg', 'عمرة الربيع');
EXECUTE insert_package_hotel(:'pkg', 'makkah', 'فندق الاستيراد', 4, NULL, 8, 0);
EXECUTE insert_package_departure(:'pkg', '2031-03-01', '2031-03-12', 40, NULL) \g /dev/null
SELECT id AS dep FROM package_departures WHERE package_id = :'pkg' \gset
EXECUTE insert_package_price(:'pkg', NULL, 'quad', 'adult', 4500, NULL);
EXECUTE insert_package_price(:'pkg', :'dep', 'triple', 'adult', 5000, NULL);
CREATE TEMP TABLE c2_search_draft AS SELECT * FROM search_packages('عمرة الربيع المستوردة');
CREATE TEMP TABLE c2_list AS EXECUTE list_packages(NULL, :'imp');
DO $$ DECLARE l record; BEGIN
  ASSERT (SELECT status FROM packages WHERE title = 'عمرة الربيع المستوردة') = 'draft', 'C2: stored as draft';
  ASSERT NOT EXISTS (SELECT 1 FROM c2_search_draft WHERE title = 'عمرة الربيع المستوردة'), 'C2: draft hidden from bot search';
  SELECT * INTO l FROM c2_list;
  ASSERT l.departures = 1 AND l.prices = 2 AND l.price_from = 4500 AND l.source_import_id IS NOT NULL,
         'C2: review list with counts and price_from';
  ASSERT (SELECT count(*) FROM package_aliases a JOIN packages p ON p.id = a.package_id
           WHERE p.title = 'عمرة الربيع المستوردة') = 1, 'C2: duplicate alias ignored';
  ASSERT NOT EXISTS (SELECT 1 FROM package_prices pp JOIN packages p ON p.id = pp.package_id
                      WHERE p.title = 'عمرة الربيع المستوردة' AND pp.currency <> 'LYD'), 'C2: prices are LYD only';
  ASSERT (SELECT seats_left FROM package_departures d JOIN packages p ON p.id = d.package_id
           WHERE p.title = 'عمرة الربيع المستوردة') IS NULL, 'C2: seats_left unknown (no invented scarcity)';
END $$;

-- ---------------------------------------------------------------- C3: التصحيح قبل الاعتماد + الاعتماد
SELECT id AS price_q FROM package_prices WHERE package_id = :'pkg' AND room_type = 'quad' \gset
EXECUTE price_for_update(:'price_q') \gset pf_
EXECUTE update_price(NULL, false, NULL, :'price_q') \g /dev/null
DO $$ BEGIN
  ASSERT (SELECT amount FROM package_prices WHERE room_type = 'quad' AND package_id =
          (SELECT id FROM packages WHERE title = 'عمرة الربيع المستوردة')) = 4500, 'C3: nothing to change => unchanged';
END $$;
EXECUTE update_price(4400, true, 'بعد التأكيد', :'price_q') \g /dev/null
EXECUTE delete_departure(:'dep');
EXECUTE package_for_publish(:'pkg') \gset pub_
EXECUTE publish_package(:'pkg') \g /dev/null
EXECUTE confirm_package_prices(:'pkg');
EXECUTE publish_package(:'pkg') \g /dev/null
EXECUTE delete_draft_package(:'pkg') \g /dev/null
CREATE TEMP TABLE c3_search AS SELECT * FROM search_packages('عمرة الربيع المستوردة');
SELECT pg_temp.check(:'pf_package_status' = 'draft', 'C3: price lookup reports its package status') \g /dev/null
SELECT pg_temp.check(:'pub_adult_prices'::int = 1, 'C3: departure delete cascaded to its price') \g /dev/null
DO $$ DECLARE p record; BEGIN
  SELECT pp.amount, pp.notes INTO p FROM package_prices pp JOIN packages k ON k.id = pp.package_id
   WHERE k.title = 'عمرة الربيع المستوردة';
  ASSERT p.amount = 4400 AND p.notes = 'بعد التأكيد', 'C3: price corrected';
  ASSERT (SELECT status FROM packages WHERE title = 'عمرة الربيع المستوردة') = 'active', 'C3: published';
  ASSERT EXISTS (SELECT 1 FROM c3_search WHERE title = 'عمرة الربيع المستوردة'), 'C3: bot search finds it after publish';
END $$;
EXECUTE finish_import('done', NULL, '["تحذير"]', 1, :'imp');
DO $$ DECLARE i record; BEGIN
  SELECT * INTO i FROM catalog_imports WHERE sha256 = repeat('a', 64);
  ASSERT i.status = 'done' AND i.file_data IS NULL AND i.packages_created = 1 AND i.warnings = '["تحذير"]'::jsonb
     AND i.finished_at IS NOT NULL, 'C3: finished import drops the file';
END $$;
SELECT set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', false) \g /dev/null
EXECUTE update_price(1, true, 'اختراق', :'price_q') \g /dev/null
EXECUTE delete_price(:'price_q');
CREATE TEMP TABLE c3_b AS EXECUTE list_packages(NULL, NULL);
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false) \g /dev/null
DO $$ BEGIN
  ASSERT (SELECT amount FROM package_prices pp JOIN packages k ON k.id = pp.package_id
           WHERE k.title = 'عمرة الربيع المستوردة') = 4400, 'C3: other tenant cannot change or delete prices';
  ASSERT NOT EXISTS (SELECT 1 FROM c3_b WHERE title = 'عمرة الربيع المستوردة'), 'C3: other tenant cannot list it';
  RAISE NOTICE 'PASS C2-C3 drafts hidden until publish, corrections, cascade, publish once, isolation';
END $$;

-- ---------------------------------------------------------------- K1: رد الموظف كمعلومة للبوت
\set REF '''conversation:aaaaaaaa-0000-0000-0000-000000000701:message:x'''
EXECUTE insert_staff_knowledge(:REF, 'هل تقبلون الدفع بالتقسيط؟', 'نعم، على دفعتين: نصف عند الحجز والباقي قبل السفر.') \g /dev/null
SELECT id AS kid FROM knowledge_chunks WHERE source_ref = :REF \gset
EXECUTE enqueue_embedding(:'kid');
EXECUTE enqueue_embedding(:'kid');
CREATE TEMP TABLE k1_fts AS EXECUTE tool_knowledge_fts('ممكن نقسط الدفع؟', 5);
CREATE TEMP TABLE k1_ref AS EXECUTE knowledge_by_source_ref(:REF);
CREATE TEMP TABLE k1_staff AS EXECUTE list_knowledge(true, false);
DO $$ DECLARE k record; BEGIN
  SELECT * INTO k FROM knowledge_chunks WHERE source_ref LIKE 'conversation:aaaaaaaa-0000-0000-0000-000000000701%';
  ASSERT k.source_type = 'faq' AND k.created_by = '99999999-0000-0000-0000-000000000003' AND k.embedding IS NULL,
         'K1: saved as faq with its author, embedding pending';
  ASSERT EXISTS (SELECT 1 FROM k1_fts WHERE title = 'هل تقبلون الدفع بالتقسيط؟'), 'K1: bot text search finds it immediately';
  ASSERT (SELECT count(*) FROM k1_ref) = 1, 'K1: found by source_ref (same reply not saved twice)';
  ASSERT (SELECT count(*) FROM ai_jobs WHERE knowledge_chunk_id = k.id AND status IN ('pending','processing')) = 1,
         'K1: one active embedding job';
  ASSERT (SELECT count(*) FROM k1_staff) = 1 AND (SELECT created_by_name FROM k1_staff) = 'شخص آخر', 'K1: staff list';
END $$;
SELECT '[' || array_to_string(array_fill(0.01::float8, ARRAY[1024]), ',') || ']' AS vec \gset
EXECUTE set_knowledge_embedding(:'vec', 'text-embedding-3-small', :'kid');
EXECUTE deactivate_knowledge(:'kid') \g /dev/null
CREATE TEMP TABLE k1_fts_after AS EXECUTE tool_knowledge_fts('ممكن نقسط الدفع؟', 5);
CREATE TEMP TABLE k1_all_inactive AS EXECUTE list_knowledge(false, true);
DO $$ BEGIN
  ASSERT (SELECT embedding IS NOT NULL AND embedding_model = 'text-embedding-3-small' FROM knowledge_chunks
           WHERE source_ref LIKE 'conversation:aaaaaaaa-0000-0000-0000-000000000701%'), 'K1: embedding stored';
  ASSERT NOT EXISTS (SELECT 1 FROM k1_fts_after WHERE title = 'هل تقبلون الدفع بالتقسيط؟'), 'K1: deactivated => bot stops using it';
  ASSERT EXISTS (SELECT 1 FROM k1_all_inactive WHERE title = 'هل تقبلون الدفع بالتقسيط؟' AND NOT is_active), 'K1: kept for history';
END $$;
SELECT set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', false) \g /dev/null
CREATE TEMP TABLE k1_b AS EXECUTE list_knowledge(false, true);
UPDATE knowledge_chunks SET is_active = true WHERE source_ref = :REF;
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', false) \g /dev/null
DO $$ BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM k1_b WHERE title = 'هل تقبلون الدفع بالتقسيط؟'), 'K1: other tenant cannot see it';
  ASSERT NOT (SELECT is_active FROM knowledge_chunks WHERE source_ref LIKE 'conversation:aaaaaaaa-0000-0000-0000-000000000701%'),
         'K1: other tenant cannot reactivate it';
  RAISE NOTICE 'PASS K1 staff answer as knowledge: author, instant FTS, one embed job, embedding, deactivate, isolation';
END $$;

\echo 'ALL PHASE 7a (APP_USER) TESTS PASSED'
