# Libya AI Commerce Agent — دليل العمل لـ Claude Code

منصة SaaS متعددة المستأجرين (multi-tenant) تقدّم وكلاء ذكاء اصطناعي لخدمة الزبائن والمبيعات للأنشطة التجارية في ليبيا، باللهجة الليبية.

- **العميل التجريبي:** وكالة حج وعمرة. الوكيل يعرض البرامج والرحلات وأسعار الغرف، ويجمع طلبات الحجز (Leads).
- **القنوات:** WhatsApp Cloud API، Messenger، Instagram. تيليجرام مستبعد. تيك توك مستقبلاً.

اقرأ هذا الملف كاملاً قبل أي تعديل، ثم `docs/HANDOFF.md` لمعرفة ما تم وما التالي.

## هيكل المستودع

```
backend/   FastAPI + PostgreSQL (المراحل 1–6a منفذة ومختبرة)
web/       لوحة التحكم Next.js (المرحلة 6b — قيد التنفيذ، غير مكتملة)
docs/architecture/  وثائق المعمارية والقرارات لكل مرحلة (01–08)
```

## الـ Stack (ثابت، لا يتغير)

- **الباكيند:**
  - Python 3.11 مع FastAPI، و SQLAlchemy 2 async مع asyncpg.
  - PostgreSQL 16 مع pgvector و pg_trgm.
  - Alembic للـ migrations.
  - Docker Compose على خادم Hetzner.
  - OpenAI gpt-4o مباشرة عبر `app/llm/`. **ممنوع LangChain.**
- **الواجهة:**
  - Next.js 15 (App Router)، React 19، TypeScript strict.
  - CSS عادي بـ variables في `app/globals.css`. لا Tailwind ولا مكتبات UI، إلا بسبب موثق.
- **Modular monolith:** الـ API والـ worker من نفس الكود.

## قواعد غير قابلة للتفاوض

1. **العزل بين الوكالات (RLS):**
   - كل جدول تابع لوكالة فيه `tenant_id` و `UNIQUE (tenant_id, id)`، والمفاتيح الأجنبية مركّبة `(tenant_id, x_id)`.
   - يُفعَّل العزل بـ `SELECT app_enable_tenant_rls('table')`.
   - سياق الوكالة يُضبط بـ `set_config('app.tenant_id', ..., true)` داخل `tenant_session()`.
   - الاختبار `tests/sql/00_audit_as_owner.sql` يفشل إذا وُجد جدول بدون RLS أو view بدون `security_invoker`.
2. **الأدوار:**
   - `app_owner`: للـ migrations.
   - `app_user`: للـ API والـ worker، وهو NOBYPASSRLS.
   - `app_admin`: للوحة الإدارة الداخلية، وهو BYPASSRLS. **المال يتغير فقط عبر دوال SECURITY DEFINER.**
3. **الدفع ليبي 100%:**
   - العملة الوحيدة LYD.
   - طرق الدفع: بوابة Plutu، وقسائم رقمية (vouchers)، وتحويل مصرفي أو رصيد مع مراجعة الإيصال.
   - **ممنوع:** Stripe، Paddle، أي عملة أجنبية، أي نظام ضرائب، أي كيان قانوني أجنبي.
   - نُصدر سندات قبض فقط (`RC-YYYY-NNNNNN`)، و `numeric(14,3)`.
4. **Idempotency:**
   - الـ webhooks: `ON CONFLICT` على معرّف الرسالة الخارجي.
   - المحفظة: `wallet_apply` مع `(source, reference_id)`.
   - رد الموظف: `messages.client_msg_id`.
5. **الـ worker:**
   - `FOR UPDATE SKIP LOCKED` مع lease، و debounce عبر `reply_due_at`، وطابور outbox مع retries و backoff.
   - **لا transaction مفتوحة أثناء استدعاء HTTP أو LLM.**
6. **الأمان:**
   - الصلاحيات تُفرض في الباكيند فقط. الواجهة لا تُخفي صلاحيات، الإخفاء فيها للراحة فقط.
   - لا secrets في الكود. كل شيء من `.env`.
   - الجلسات opaque، ونخزن sha256 التوكن فقط.
   - كلمات المرور بـ scrypt. الكوكي httpOnly مع فحص Origin (CSRF)، أو Bearer `ses_` للجوال.
7. **SQL داخل `text()`:** استخدم `CAST(:x AS type)` وليس `:x::type`، لأن الثانية تكسر bind في SQLAlchemy.
8. **نصوص الزبائن** باللهجة الليبية، **ونصوص اللوحة** بالعربية مع RTL.
9. **لا تدّعي:**
   - نجاح اختبار لم يُشغَّل.
   - اكتمال ميزة هي mock فقط.
   - ولا تكتب TODO بدل التنفيذ.
10. **لا تغيّر قرارات مرحلة سابقة** بدون سبب موثق في `docs/architecture/`.
11. **لا تبنِ ميزات مراحل مستقبلية** إلا للضرورة: Agent Studio، التحليلات المتقدمة، Plutu، واجهة القسائم، PDF، تطبيق الجوال.
12. **اسأل سؤالاً واحداً فقط** إذا نقص قرار حرج. غير ذلك: اتخذ القرار الأوضح ووثّقه.

## التشغيل والاختبار

```bash
# الباكيند
cd backend && cp .env.example .env        # عبّئ القيم
docker compose up -d db
alembic upgrade head
pytest -q                                  # unit + api
OWNER_URL=... APP_USER_URL=... ADMIN_URL=... ./scripts/run_sql_tests.sh   # RLS + المال + Inbox + NOTIFY
uvicorn app.main:app --reload --port 8000
python -m app.worker.runner                # الـ worker

# الواجهة
cd web && cp .env.example .env.local      # API_URL=http://127.0.0.1:8000
npm install && npm run typecheck && npm run build
npm run dev                                # http://localhost:3000
```

- `WEB_ALLOWED_ORIGINS` في `.env` الباكيند يجب أن يشمل `http://localhost:3000`.
- `scripts/run_sql_tests.sh` يحتاج python (يولّد PREPARE من الاستعلامات الحقيقية).

## Definition of Done لكل مرحلة

- **الكود:** production-ready، يعيد استخدام الأنماط الموجودة، ويحافظ على RLS والأدوار.
- **قاعدة البيانات:** migration مع downgrade كامل.
- **الاختبارات:** تُشغَّل فعلياً. SQL لكل سلوك قاعدة بيانات، و unit للمنطق.
- **الواجهة:** `npm run build` ينجح، وكل صفحة فيها حالات loading و empty و error، وتدعم RTL والعربية والجوال.
- **التوثيق:** `docs/architecture/NN-phase-X.md`، مع تحديث README و `docs/HANDOFF.md`.
- **الملخص النهائي** بالعربية وبهذه الأقسام: تم إنجازه / الملفات الرئيسية / الاختبارات التي تم تشغيلها فعلياً / النتيجة / ملاحظات.
- **Git:** commits صغيرة وواضحة، ورسائلها بالإنجليزية.
