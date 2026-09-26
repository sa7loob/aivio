# Libya AI Commerce Agent — Backend (Phase 2: Database)

## التشغيل

```bash
cp .env.example .env            # غيّر كلمات المرور
docker compose up -d postgres   # ينشئ الأدوار والإضافات عند أول تشغيل
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)
alembic upgrade head

# اختبارات العزل والبحث (تحتاج psql)
OWNER_URL=postgresql://app_owner:$APP_OWNER_PASSWORD@127.0.0.1:5432/agentdb \
APP_USER_URL=postgresql://app_user:$APP_USER_PASSWORD@127.0.0.1:5432/agentdb \
./scripts/run_sql_tests.sh
```

## الأدوار

| الدور | الاستخدام | RLS |
|---|---|---|
| `postgres` | init script فقط (أدوار + إضافات) | — |
| `app_owner` | Alembic + مهام إدارية (إنشاء وكالة) | مالك الجداول، لا يخضع |
| `app_user` | API + Worker | يخضع دائماً (NOBYPASSRLS) |

## الـ Migrations

| Revision | المحتوى |
|---|---|
| 0001 | الإضافات، `app_current_tenant_id()`, `app_normalize_ar()`, `app_enable_tenant_rls()`, الصلاحيات الافتراضية |
| 0002 | tenants, staff_users, channel_accounts, contacts, conversations, messages, webhook_events, outbound_messages + دوال الـ worker |
| 0003 | packages, package_aliases, package_hotels, package_departures, package_prices, knowledge_chunks + `v_package_offers` + `search_packages()` |
| 0004 | leads, lead_events |

## قواعد لكل جدول جديد

1. عمود `tenant_id uuid NOT NULL` + `UNIQUE (tenant_id, id)`.
2. المفاتيح الأجنبية مركّبة: `FOREIGN KEY (tenant_id, x_id) REFERENCES x (tenant_id, id)`.
3. `SELECT app_enable_tenant_rls('table_name');`
4. أي View: `WITH (security_invoker = true)`.
5. `tests/sql/00_audit_as_owner.sql` يفشل إذا نُسيت 3 أو 4.
