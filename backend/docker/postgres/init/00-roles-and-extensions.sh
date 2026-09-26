#!/bin/bash
# يعمل مرة واحدة فقط عند إنشاء الـ volume لأول مرة (docker-entrypoint-initdb.d)
# ويعمل كمستخدم superuser. هنا فقط ما يحتاج صلاحيات superuser:
#   - إنشاء الأدوار (Roles) على مستوى الـ cluster
#   - تثبيت الإضافات (vector ليست trusted extension)
# كل ما عدا ذلك يتم عبر Alembic بدور app_owner.
set -euo pipefail

: "${APP_OWNER_PASSWORD:?APP_OWNER_PASSWORD is required}"
: "${APP_USER_PASSWORD:?APP_USER_PASSWORD is required}"
: "${APP_ADMIN_PASSWORD:?APP_ADMIN_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- مالك الجداول: يُستخدم للـ migrations والمهام الإدارية فقط
    CREATE ROLE app_owner LOGIN PASSWORD '${APP_OWNER_PASSWORD}';

    -- دور التشغيل (API + Worker): لا يملك الجداول ولا يتجاوز RLS
    CREATE ROLE app_user LOGIN PASSWORD '${APP_USER_PASSWORD}'
        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

    -- دور لوحة الإدارة الداخلية: يرى كل الوكالات (BYPASSRLS)، بدون DDL وبدون تعديل مباشر للأرصدة
    CREATE ROLE app_admin LOGIN PASSWORD '${APP_ADMIN_PASSWORD}'
        NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;

    ALTER DATABASE "${POSTGRES_DB}" OWNER TO app_owner;
    ALTER SCHEMA public OWNER TO app_owner;
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    GRANT USAGE ON SCHEMA public TO app_user, app_admin;

    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EOSQL
