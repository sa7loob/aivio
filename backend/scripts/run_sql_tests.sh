#!/usr/bin/env bash
# يشغّل اختبارات قاعدة البيانات بعد: alembic upgrade head
# المتطلبات: psql + OWNER_URL و APP_USER_URL و ADMIN_URL (بصيغة postgresql://...)
set -euo pipefail
cd "$(dirname "$0")/.."

: "${OWNER_URL:?e.g. postgresql://app_owner:pass@127.0.0.1:5432/agentdb}"
: "${APP_USER_URL:?e.g. postgresql://app_user:pass@127.0.0.1:5432/agentdb}"
: "${ADMIN_URL:?e.g. postgresql://app_admin:pass@127.0.0.1:5432/agentdb}"

reset_due() {  # محادثة النور تصبح مستحقة الرد وبدون lease (لاختبار إيقاف البوت)
  psql "$OWNER_URL" -X -q -c "UPDATE conversations SET reply_lease_until = NULL,
      reply_due_at = now() - interval '1 second' WHERE id = 'aaaaaaaa-0000-0000-0000-000000000011'"
}

psql "$OWNER_URL"    -X -q -f tests/sql/00_audit_as_owner.sql
psql "$OWNER_URL"    -X -q -f tests/sql/01_seed_as_owner.sql
psql "$APP_USER_URL" -X -q -f tests/sql/02_rls_as_app_user.sql
psql "$APP_USER_URL" -X -q -f tests/sql/03_search_as_app_user.sql
psql "$ADMIN_URL"    -X -q -f tests/sql/04_billing_admin_part1.sql
reset_due
psql "$APP_USER_URL" -X -q -f tests/sql/05_billing_user_part1.sql
psql "$ADMIN_URL"    -X -q -f tests/sql/06_billing_admin_part2.sql
reset_due
psql "$APP_USER_URL" -X -q -f tests/sql/07_billing_user_part2.sql
psql "$APP_USER_URL" -X -q -f tests/sql/08_identity_as_app_user.sql
psql "$APP_USER_URL" -X -q -f tests/sql/09_meta_compliance_as_app_user.sql
psql "$ADMIN_URL"    -X -q -c "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES
    ('99999999-0000-0000-0000-000000000002', 'reset-ok',  now() + interval '1 day'),
    ('99999999-0000-0000-0000-000000000002', 'reset-old', now() - interval '1 minute')"
psql "$APP_USER_URL" -X -q -f tests/sql/10_password_reset_as_app_user.sql

# ---- المرحلة 6: Inbox + Leads + Realtime (الاستعلامات الحقيقية من الكود كـ PREPARE)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PY="${PYTHON:-python}"
"$PY" scripts/gen_prepared_sql.py app.dashboard.queries > "$TMP/dash.sql"
"$PY" scripts/gen_prepared_sql.py app.db.queries ENQUEUE_OUTBOUND,INSERT_AGENT_RUN > "$TMP/db.sql"
psql "$OWNER_URL" -X -q -c "INSERT INTO memberships (tenant_id, user_id, role) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001', '99999999-0000-0000-0000-000000000003', 'agent');
  INSERT INTO staff_users (id, tenant_id, user_id, full_name, role) VALUES
    ('aaaaaaaa-0000-0000-0000-0000000000f3', 'aaaaaaaa-0000-0000-0000-000000000001',
     '99999999-0000-0000-0000-000000000003', 'موظف النور (حساب)', 'sales')"
psql "$APP_USER_URL" -X -q -v dash_sql="$TMP/dash.sql" -v db_sql="$TMP/db.sql" \
     -f tests/sql/11_inbox_leads_as_app_user.sql
psql "$APP_USER_URL" -X -q -f tests/sql/12_realtime_notify_as_app_user.sql > "$TMP/notify.out" 2>&1
n() { grep -c "$1" "$TMP/notify.out" || true; }
[ "$(n '"type" : "message", ')" -ge 1 ]      || { cat "$TMP/notify.out"; echo "FAIL T1 message notify"; exit 1; }
[ "$(n '"type" : "conversation"')" -ge 1 ]   || { cat "$TMP/notify.out"; echo "FAIL T1 unread notify"; exit 1; }
[ "$(n '"type" : "lead"')" -eq 1 ]           || { cat "$TMP/notify.out"; echo "FAIL T1 lead notify"; exit 1; }
[ "$(n '"type" : "message_status"')" -eq 1 ] || { cat "$TMP/notify.out"; echo "FAIL T1 status notify"; exit 1; }
[ "$(n 'tenant_events')" -eq 4 ]             || { cat "$TMP/notify.out"; echo "FAIL T2 worker-only update must not notify"; exit 1; }
if grep -q "NOTIFY-TEST\|218917778888" "$TMP/notify.out"; then echo "FAIL T3 notify leaked data"; exit 1; fi
echo "PASS T1-T3 realtime NOTIFY: ids only, per event type, no noise from worker fields"
echo "✔ database tests passed"
