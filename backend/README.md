# Libya AI Commerce Agent — Backend

المرحلة 2: قاعدة البيانات · المرحلة 3: النواة الخلفية + واتساب · المرحلة 4: الـ Agent · المرحلة 4.5: لوحة الإدارة والمحفظة الليبية · المرحلة 5: الهوية والربط الذاتي · المرحلة 6a: API اللوحة · المرحلة 7a: الصوت والبروشور والمعرفة من ردود الموظفين

## الهيكل

```
app/
  main.py                  FastAPI app (+ /healthz)
  core/config.py           الإعدادات من .env (pydantic-settings)
  core/crypto.py           تشفير توكنات القنوات (Fernet)
  api/webhooks.py          GET/POST /webhooks/meta
  channels/base.py         InboundMessage / StatusUpdate / ChannelSendError
  channels/meta_signature  التحقق من X-Hub-Signature-256
  channels/whatsapp.py     Parser + WhatsAppClient (httpx)
  channels/registry.py     object type -> parser ، channel -> sender
  db/tenant.py             tenant_session / system_session
  db/queries.py            كل SQL المستخدم في مكان واحد
  worker/runner.py         الحلقات: ingest / reply / send / voice / ai (+ billing و channel-health دورياً)
  worker/ingest.py         webhook_events -> contacts/conversations/messages (+debounce)
  worker/reply.py          محادثات مستحقة -> Agent -> outbound_messages (بدون tx أثناء الـ LLM)
  worker/sender.py         outbound_messages -> WhatsApp Cloud API
  llm/base.py              عقود LLM محايدة (ChatMessage, ToolCall, LLMClient)
  llm/openai_client.py     OpenAI SDK: chat + embeddings + تفريغ الصوت + استخراج JSON من صورة/PDF
  agent/core.py            حلقة LLM <-> الأدوات
  agent/tools.py           search_packages, get_package_details, search_knowledge, create_lead, handoff_to_human
  agent/prompts.py         System prompt باللهجة الليبية
  agent/history.py         تحويل الرسائل المخزنة إلى تاريخ المحادثة
  agent/knowledge_ingest   تقطيع + embeddings + إدخال في knowledge_chunks
evals/                     cases.yaml + run.py (تقييم بالموديل الحقيقي)
migrations/versions/       0001..0012
app/admin/                 Admin API الداخلي (tenants, channels, billing, vouchers)
app/billing/               رموز القسائم + تحويل أخطاء المال إلى HTTP
app/identity/              كلمات المرور (scrypt) والجلسات + SQL الهوية
app/onboarding/            الربط الذاتي: Embedded Signup + Facebook Login
app/api/v1/                auth / team / onboarding / channels / inbox / leads / events / catalog / knowledge
app/dashboard/             SQL ومنطق اللوحة (Inbox + Leads)  ·  app/realtime/  LISTEN/NOTIFY -> SSE
app/ai/                    المرحلة 7a: طابور ai_jobs، تفريغ الصوت، البروشور -> مسودات، ردود الموظفين -> معرفة
app/channels/messenger.py  ماسنجر + إنستغرام (parsers + sender)
app/web/connect.html       صفحة الربط المؤقتة (/connect)
docs/phases/               وثائق المراحل
scripts/                   register_whatsapp_channel ، ingest_knowledge ، run_sql_tests.sh
scripts/dev_*              للتطوير فقط: بديل Meta + OpenAI (dev_mock_upstream) ، webhook موقّع (dev_send_whatsapp) ، تجربة 7a (dev_try_7a.sh)
tests/unit, tests/api      pytest ، tests/sql اختبارات قاعدة البيانات
```

## التشغيل المحلي

```bash
cp .env.example .env        # املأ META_APP_SECRET, META_VERIFY_TOKEN, TOKEN_ENCRYPTION_KEY
docker compose up -d --build   # postgres -> migrate -> api + worker

# تسجيل وكالة تجريبية + رقم الاختبار (مرة واحدة)
pip install -r requirements-dev.txt
export $(grep -v '^#' .env | xargs)
python -m scripts.register_whatsapp_channel \
    --tenant-slug noor-travel --tenant-name "وكالة تجريبية" \
    --phone-number-id <PHONE_NUMBER_ID> --waba-id <WABA_ID> --test
```

## ربط Meta (رقم الاختبار)

1. Meta يشترط رابط HTTPS عام. للتطوير: tunnel إلى `127.0.0.1:8000`
   (مثل `cloudflared tunnel --url http://localhost:8000`).
2. في لوحة التطبيق: WhatsApp > Configuration > Webhook
   - Callback URL: `https://<tunnel>/webhooks/meta`
   - Verify token: نفس `META_VERIFY_TOKEN`
   - اشترك في الحقل `messages`.
3. أضف رقم هاتفك كمستلم في قائمة أرقام الاختبار، ثم أرسل رسالة للرقم التجريبي.
4. المتوقع: خلال ~4 ثوانٍ (debounce) يصلك الرد التجريبي.
   تابع: `docker compose logs -f worker`.

## المرحلة 4: الـ Agent

```bash
# قاعدة المعرفة لوكالة (YAML: title / source_type / content)
python -m scripts.ingest_knowledge --tenant-slug noor-travel --file knowledge/noor.yaml

# التقييم بالموديل الحقيقي على وكالة تجريبية (لا يرسل لواتساب)
python -m evals.run                    # كل الحالات
python -m evals.run --case booking_flow --judge
python -m evals.run --model gpt-4o-mini   # مقارنة موديل أرخص
```

- قالب `new_lead` يجب إنشاؤه واعتماده في WhatsApp Manager (فئة Utility، 4 متغيرات).
- كل تشغيل للـ Agent يُسجَّل في `agent_runs` (التوكنز، الزمن، الأدوات، الأخطاء).
- أي تعديل في `prompts.py` أو الأدوات => شغّل `evals.run` قبل النشر.

## المرحلة 4.5: لوحة الإدارة الداخلية

```bash
# قاعدة موجودة مسبقاً فقط: إنشاء الدور app_admin مرة واحدة
docker compose exec -T postgres psql -U postgres -d agentdb \
    -v admin_password="'<strong-password>'" -f - < docker/postgres/manual/create_app_admin_role.sql
docker compose up -d --build            # migrate (0007, 0008) + admin-api على 127.0.0.1:8001

python -m scripts.create_admin --email ops@example.ly --name "مدير المنصة" --role superadmin
# الوصول من جهازك: ssh -L 8001:127.0.0.1:8001 server  ثم  http://localhost:8001/docs
```

يوم تشغيل مكتب جديد: `POST /admin/plans`، ثم `POST /admin/tenants`، ثم `POST .../channels/whatsapp`، ثم `POST .../wallet/credit` أو `manual-payments`، ثم `POST .../subscription/renew`.

## المرحلة 5: الهوية والربط الذاتي

```bash
# 1) خطة التسجيل الذاتي (نفس SIGNUP_PLAN_CODE)
curl -X POST localhost:8001/admin/plans -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
     -d '{"code":"starter","name":"البداية","prices":[{"period_months":1,"price_lyd":"150"}]}'
# 2) إعدادات Meta في .env: META_APP_ID, META_ES_CONFIG_ID, META_FB_LOGIN_CONFIG_ID, WEB_ALLOWED_ORIGINS
# 3) المالك يفتح https://<api>/connect  => تسجيل => ربط واتساب / فيسبوك / إنستغرام
# مكتب سُجّل يدوياً: POST /admin/tenants/{id}/owner-invitation  ثم  https://<api>/connect?invite=<token>
```

روابط Meta المطلوبة للمراجعة (App Dashboard > Settings):
`https://<api>/meta/deauthorize` و`https://<api>/meta/data-deletion`.
نسيان كلمة المرور: `POST /admin/users/password-reset {"email": ...}` ثم يُرسل الرابط للمالك.

التفاصيل والقيود: `docs/phases/phase-5.md`.

## المرحلة 6a: API لوحة التحكم (Inbox + استلام بشري + Leads + Realtime)

```bash
alembic upgrade head        # 0011
# كل الطلبات: كوكي الجلسة (أو Bearer ses_...) + X-Tenant-ID
GET  /api/v1/conversations?view=open|human|bot|unread|closed|all&assigned=me|unassigned|any&q=&cursor=
POST /api/v1/conversations/{id}/takeover | release | close | read | assign
POST /api/v1/conversations/{id}/messages   {"text": "...", "client_msg_id": "<uuid>"}
GET  /api/v1/leads?status=&assigned=any|unassigned|<staff_id>&from_date=&to_date=&q=&cursor=
PATCH /api/v1/leads/{id}  ·  POST /api/v1/leads/{id}/notes  ·  GET /api/v1/leads/export.csv (admin+)
GET  /api/v1/events?tenant=<id>            # SSE (EventSource)
```

خلف nginx: مسار `/api/v1/events` يحتاج `proxy_buffering off` و`proxy_read_timeout 1h`.
التفاصيل: `docs/phases/phase-6.md`.

البحث `q` في المحادثات والطلبات يقبل الرقم بأي صيغة محلية (`0913334444` أو `091 333 4444` أو `00218...`).

## المرحلة 7a: بوت يفهم ويتعلّم

```bash
alembic upgrade head        # 0012: ai_jobs + catalog_imports
# الـ worker يفرّغ الرسائل الصوتية تلقائياً (حلقة voice) ويستخرج البروشورات ويحسب الـ embeddings (حلقة ai)
POST /api/v1/catalog/imports?filename=brochure.jpg   (الملف = جسم الطلب، Content-Type: image/jpeg|png|webp أو application/pdf)
GET  /api/v1/catalog/imports/{id}                     # الحالة + التحذيرات + البرامج المسودة
PATCH /api/v1/catalog/prices/{id}  ·  DELETE /api/v1/catalog/prices/{id} | departures/{id} | packages/{id}   (مسودات فقط)
POST /api/v1/catalog/packages/{id}/publish            # بعده فقط يراه البوت
GET  /api/v1/conversations/{id}/knowledge-suggestion  ·  POST /api/v1/conversations/{id}/knowledge {question, answer}
GET  /api/v1/knowledge?source=staff  ·  DELETE /api/v1/knowledge/{id}
```

- الإعدادات (كلها بقيم افتراضية): `VOICE_TRANSCRIPTION_ENABLED`، `TRANSCRIPTION_MODEL`، `TRANSCRIPTION_MAX_WAIT_SECONDS`، `MEDIA_TMP_DIR`، `CATALOG_EXTRACTION_MODEL`، `CATALOG_IMPORT_MAX_BYTES`.
- الصوت لا يُخزَّن أبداً: ملف مؤقت 0600 يُحذف بعد التفريغ، ويبقى النص فقط.
- تجربة كاملة محلياً بأمر واحد: `./scripts/dev_try_7a.sh [brochure.jpg]`.
- القرارات، والتحقق، وأوامر التجربة (ومنها التجربة بموديل OpenAI الحقيقي): `docs/architecture/10-phase-7-smart-sales-and-ingestion.md`.

## التجربة المحلية بدون Meta و OpenAI (للتطوير فقط)

```bash
.venv/bin/python -m scripts.dev_mock_upstream 8099 &
export META_GRAPH_BASE_URL=http://127.0.0.1:8099 OPENAI_BASE_URL=http://127.0.0.1:8099/v1 OPENAI_API_KEY=sk-mock
# شغّل الـ API والـ worker بنفس البيئة، ثم سجّل قناة تجريبية (register_whatsapp_channel) وأرسل رسالة زبون:
.venv/bin/python -m scripts.dev_send_whatsapp --from 218913334444 --name "أبو محمد" \
    --text "قداش عمرة رمضان؟" --phone-number-id <PHONE_NUMBER_ID>
```

- البديل يرد باللهجة الليبية، و«احجز» في رسالة الزبون تستدعي `create_lead`.
- `GET /__mock/stats` يعيد عدد استدعاءات الـ LLM والإرسال.
- `POST /__mock/fail-sends {"on": true}` يجعل الإرسال يفشل (لتجربة إعادة المحاولة).
- رسالة صوتية: `dev_send_whatsapp --audio MOCKMEDIA-1` بدل `--text`. `POST /__mock/transcript {"text", "delay_seconds"}` يحدد نص التفريغ وتأخيره، و`POST /__mock/media {"path"}` يعيد ملف صوت حقيقياً عند التنزيل.
- التفاصيل: `docs/architecture/09-phase-6b-dashboard-ui.md`.

## الاختبارات

```bash
pytest -q                               # unit + api (بدون قاعدة بيانات؛ لا يتأثر بتحميل .env في الـ shell)
OWNER_URL=postgresql://app_owner:$APP_OWNER_PASSWORD@127.0.0.1:5432/agentdb \
APP_USER_URL=postgresql://app_user:$APP_USER_PASSWORD@127.0.0.1:5432/agentdb \
ADMIN_URL=postgresql://app_admin:$APP_ADMIN_PASSWORD@127.0.0.1:5432/agentdb \
./scripts/run_sql_tests.sh              # RLS + البحث + المال + Inbox/Leads + NOTIFY + ai_jobs/البروشور/المعرفة (يحتاج python لتوليد PREPARE)
```

شغّل اختبارات SQL على قاعدة فارغة بعد `alembic upgrade head`، وبدون worker يعمل عليها. بعض الفحوص تعدّ الصفوف في كل القاعدة (مثل T8)، فتفشل على قاعدة فيها بيانات تجربة.

## الأدوار

| الدور | الاستخدام | RLS |
|---|---|---|
| `postgres` | init script فقط (أدوار + إضافات) | — |
| `app_owner` | Alembic + السكربتات | مالك الجداول، لا يخضع |
| `app_user` | API + Worker | يخضع دائماً (NOBYPASSRLS) |
| `app_admin` | Admin API الداخلي | BYPASSRLS، والمال عبر الدوال فقط |

## قواعد لكل جدول جديد

1. عمود `tenant_id uuid NOT NULL` + `UNIQUE (tenant_id, id)`.
2. المفاتيح الأجنبية مركّبة: `FOREIGN KEY (tenant_id, x_id) REFERENCES x (tenant_id, id)`.
3. `SELECT app_enable_tenant_rls('table_name');`
4. أي View: `WITH (security_invoker = true)`.
5. `tests/sql/00_audit_as_owner.sql` يفشل إذا نُسيت 3 أو 4.
6. في SQL داخل `text()` استخدم `CAST(:x AS type)` وليس `:x::type`.
