-- اختبارات البحث باللهجة الليبية — يُشغَّل بدور app_user.
\set ON_ERROR_STOP on
\set QUIET on

DO $$
BEGIN
  -- التوحيد
  ASSERT app_normalize_ar('عُمْرَة رمضانـــ ١٤٤٨') = 'عمره رمضان 1448', 'N1: normalize';
  ASSERT app_normalize_ar('إسطنبول  آمنة') = 'اسطنبول امنه', 'N2: normalize alef/ta marbuta';
  RAISE NOTICE 'PASS N normalization';
END $$;

CREATE TEMP TABLE search_cases (q text, expected uuid, note text);
INSERT INTO search_cases VALUES
  ('قداش عمرة رمضان؟',              'aaaaaaaa-0000-0000-0000-0000000000a1', 'dialect + alias'),
  ('نبي نسأل على العشر الاواخر',     'aaaaaaaa-0000-0000-0000-0000000000a1', 'alias inside long message'),
  ('عمره رمضن',                      'aaaaaaaa-0000-0000-0000-0000000000a1', 'typo'),
  ('عمرة المولد',                    'aaaaaaaa-0000-0000-0000-0000000000a2', 'title with diacritics in DB'),
  ('بكم عمرة شهر ٩',                 'aaaaaaaa-0000-0000-0000-0000000000a2', 'Arabic digits alias'),
  ('الحج',                           'aaaaaaaa-0000-0000-0000-0000000000a3', 'hajj');

DO $$
DECLARE c record; top record; fails int := 0;
BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  FOR c IN SELECT * FROM search_cases LOOP
    SELECT * INTO top FROM search_packages(c.q) LIMIT 1;
    IF top.package_id IS DISTINCT FROM c.expected THEN
      fails := fails + 1;
      RAISE WARNING 'FAIL search "%" (%): got % score %', c.q, c.note, top.title, top.score;
    ELSE
      RAISE NOTICE 'PASS search "%" -> % (score %, alias %)', c.q, top.title, round(top.score::numeric, 2), top.matched_alias;
    END IF;
  END LOOP;
  -- العزل: برنامج الصفا "عمرة رمضان الاقتصادية" لا يظهر للنور
  ASSERT NOT EXISTS (SELECT 1 FROM search_packages('عمرة رمضان الاقتصادية', 20)
                     WHERE package_id = 'bbbbbbbb-0000-0000-0000-0000000000b1'), 'S: foreign package in search';
  -- المسودات لا تظهر
  ASSERT NOT EXISTS (SELECT 1 FROM search_packages('رحلة اسطنبول', 20)), 'S: draft package in search';
  -- كلام بلا علاقة لا يعيد نتائج
  ASSERT NOT EXISTS (SELECT 1 FROM search_packages('السلام عليكم', 20)), 'S: greeting matched a package';
  -- رموز خاصة لا تكسر الاستعلام
  PERFORM * FROM search_packages($q$ عمرة'رمضان & (!| :* \ $q$);
  ASSERT fails = 0, format('S: %s search cases failed', fails);
  RAISE NOTICE 'PASS S isolation, drafts, noise, special chars';
END $$;

-- الوكالة الثانية ترى برنامجها هي لنفس السؤال
DO $$
DECLARE top record;
BEGIN
  PERFORM set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000002', true);
  SELECT * INTO top FROM search_packages('قداش عمرة رمضان؟') LIMIT 1;
  ASSERT top.package_id = 'bbbbbbbb-0000-0000-0000-0000000000b1', 'S2: tenant B search';
  RAISE NOTICE 'PASS S2 same query, other tenant -> own package';
END $$;

\echo 'ALL SEARCH TESTS PASSED'
