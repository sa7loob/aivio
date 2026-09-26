-- تدقيق هيكلي: يفشل إذا أُضيف جدول فيه tenant_id بدون RLS، أو View بدون security_invoker.
-- شغّله في CI بعد كل migration.
\set ON_ERROR_STOP on
\set QUIET on

DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(c.relname, ', ') INTO bad
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
  WHERE c.relkind = 'r'
    AND (EXISTS (SELECT 1 FROM pg_attribute a
                 WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
         OR c.relname = 'tenants')
    AND (NOT c.relrowsecurity
         OR NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid));
  ASSERT bad IS NULL, format('Tables with tenant_id but no RLS policy: %s', bad);

  SELECT string_agg(c.relname, ', ') INTO bad
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
  WHERE c.relkind = 'v'
    AND NOT coalesce('security_invoker=true' = ANY (c.reloptions), false);
  ASSERT bad IS NULL, format('Views without security_invoker: %s', bad);

  -- دور التشغيل يجب ألا يتجاوز RLS
  ASSERT NOT (SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = 'app_user'),
         'app_user must not be superuser or BYPASSRLS';
  RAISE NOTICE 'PASS audit: every tenant table has RLS, views are security_invoker, app_user is restricted';
END $$;
