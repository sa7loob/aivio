# Libya AI Commerce Agent

منصة SaaS لوكلاء ذكاء اصطناعي (خدمة زبائن + مبيعات) للأنشطة التجارية في ليبيا — واتساب، ماسنجر، إنستغرام.

> **منذ 2026-09-26:** المشروع تحوّل إلى وكالة أتمتة على n8n. انظر `agency/README.md` و `docs/architecture/11-agency-n8n-multi-client.md`. منصة SaaS أدناه متوقفة وتبقى للمرجع.

- `backend/` — FastAPI + PostgreSQL (RLS متعدد المستأجرين)، الـ Agent، الـ worker، الفوترة بالدينار الليبي. انظر `backend/README.md`.
- `web/` — لوحة التحكم (Next.js، عربي RTL، تعمل على الجوال): المحادثات اللحظية والاستلام من البوت، طلبات الحجز، الفريق والدعوات، القنوات.
- `agency/` — **الاتجاه الحالي:** n8n لكل عميل + Caddy (HTTPS) + Postgres، وسكربتات إضافة عميل والنسخ الاحتياطي.
- `docs/architecture/` — المعمارية والقرارات لكل مرحلة (01–11).
- `docs/HANDOFF.md` — الحالة الحالية وما التالي.
- `CLAUDE.md` — قواعد العمل على المشروع.

## التشغيل المحلي

```bash
# الباكيند (التفاصيل في backend/README.md)
cd backend && cp .env.example .env         # عبّئ القيم؛ WEB_ALLOWED_ORIGINS يشمل http://localhost:3000
docker compose up -d postgres
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
set -a; . ./.env; set +a && .venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --port 8000      # + الـ worker: .venv/bin/python -m app.worker.runner

# الواجهة
cd ../web && cp .env.example .env.local       # API_URL=http://127.0.0.1:8000
npm install && npm run typecheck && npm run build
npm run dev                                 # http://localhost:3000
```

- **بدون حساب Meta أو مفتاح OpenAI:** يعمل التدفق كاملاً مع البديل المحلي `scripts/dev_mock_upstream.py`.
- **التجربة الشاملة للوحة:** `web/e2e/dashboard.e2e.js`.
- الطريقة في `docs/architecture/09-phase-6b-dashboard-ui.md`.

## الاختبارات

| الأمر | ماذا يختبر |
|---|---|
| `cd backend && pytest -q` | unit + api |
| `cd backend && ./scripts/run_sql_tests.sh` | RLS، والمال، و Inbox/Leads، و NOTIFY، وطابور الذكاء الاصطناعي والبروشور والمعرفة. يحتاج `OWNER_URL` و `APP_USER_URL` و `ADMIN_URL` |
| `cd backend && ./scripts/dev_try_7a.sh` | تجربة 7a كاملة مع البديل المحلي (الوثيقة 10، قسم «التجربة») |
| `cd web && npm run typecheck && npm run build` | الواجهة |
| `cd web && OWNER_URL=... node e2e/dashboard.e2e.js` | اللوحة مع الباكيند الحقيقي. يحتاج Playwright |
