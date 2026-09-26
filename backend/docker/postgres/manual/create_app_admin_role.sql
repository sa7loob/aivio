-- لقواعد البيانات الموجودة مسبقاً (init script لا يعمل إلا عند إنشاء الـ volume أول مرة).
-- شغّله مرة واحدة كـ superuser قبل alembic upgrade إلى 0007:
--   docker compose exec -T postgres psql -U postgres -d agentdb \
--       -v admin_password="'<strong-password>'" -f - < docker/postgres/manual/create_app_admin_role.sql
CREATE ROLE app_admin LOGIN PASSWORD :admin_password
    NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_admin;
