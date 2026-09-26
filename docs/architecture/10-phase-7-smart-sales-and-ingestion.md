# المرحلة 7: بوت يفهم ويتعلّم (7a) + موظف مبيعات (7b)

> وثيقة قرار — 2026-09-26.
> الحالة: **7a منفذة في الباكيند ومختبرة محلياً** (مع بديل محلي لـ Meta و OpenAI؛ انظر «ما تم التحقق منه» و«التجربة»).
> واجهة 7a في اللوحة لم تُبنَ بعد. 7b لم تبدأ.

## لماذا غيّرنا المرحلة 7

خارطة الطريق الأصلية كانت تضع «Agent Studio» (تعديل البرامج والأسعار وقاعدة المعرفة من اللوحة) في المرحلة 7.
مراجعة الكود بعد 6b كشفت مشكلتين أهم من Agent Studio:

1. **الوكالة الجديدة تبدأ بكتالوج فارغ.**
   - التسجيل والربط ذاتيان، لكن البرامج والأسعار تُدخل بسكربتات YAML فقط.
   - لذلك يرد البوت على زبائن وكالة مسجّلة حديثاً بدون أي سعر.
2. **البوت لا يفهم الرسائل الصوتية.**
   - الصوت يُخزَّن، لكن الوكيل يرى مكانه `[رسالة صوتية]` فقط.
   - في ليبيا نسبة كبيرة من الزبائن تتكلم ولا تكتب.

القرار: نقسم المرحلة 7 إلى جزأين.

| الجزء | المحتوى | الحالة |
|---|---|---|
| **7a — بوت يفهم ويتعلّم** | تفريغ الرسائل الصوتية، استخراج الكتالوج من البروشور كمسودة مع اعتماد بشري، «احفظ ردك كمعلومة للبوت» | الباكيند منفذ ومختبر؛ الواجهة لاحقاً |
| **7b — موظف مبيعات** | متابعة داخل نافذة 24 ساعة، الزبون الساخن وتنبيه المهلة، التقرير الصباحي على واتساب، قائمة الإعداد و«جرّب بوتك»، سقف التجربة بالمحادثات | بعد التحقق من 7a |

Agent Studio الكامل لم يُلغَ. سيُبنى تدريجياً فوق شاشة اعتماد البروشور بدل جداول إدخال فارغة.

## 7a: القرارات

### 1. طابور مهام واحد للذكاء الاصطناعي: `ai_jobs`

- جدول واحد لكل المهام البطيئة أو المكلفة. أنواعه: `transcribe` و `catalog_extract` و `embed_knowledge`.
- نفس نمط `outbound_messages`:
  - `FOR UPDATE SKIP LOCKED` مع lease.
  - retries مع backoff.
  - دالة `claim_ai_jobs` بصلاحية SECURITY DEFINER، تعيد معرّفات فقط، ثم تتم المعالجة داخل `tenant_session`.
- **حلقتان منفصلتان في الـ worker:**
  - `voice` للتفريغ، لأنه حساس لزمن الرد.
  - `ai` لاستخراج الكتالوج والـ embeddings، لأنهما أبطأ ولا يجب أن يؤخرا الصوت.
- **الوكالة الموقوفة** (`subscriptions.status` = suspended أو cancelled): مهامها لا تُؤخذ، فلا إنفاق على الذكاء الاصطناعي. هذا نفس سلوك إيقاف البوت منذ المرحلة 4.5.
- **لا transaction مفتوحة أثناء استدعاء Meta أو OpenAI** (القاعدة 5). التسلسل دائماً: tx قصيرة للقراءة، ثم الاستدعاء الخارجي، ثم tx قصيرة للكتابة.

### 2. تفريغ الرسائل الصوتية

**المسار:**

1. الـ webhook يستلم الرسالة كالعادة.
2. ingest يحفظها كرسالة `audio`، وينشئ مهمة `transcribe` في نفس الـ transaction.
3. حلقة `voice` تنزّل الصوت:
   - واتساب عبر `GET /{media_id}` ثم الرابط مع التوكن.
   - ماسنجر وإنستغرام عبر رابط المرفق.
4. يُكتب الصوت في ملف مؤقت ويُمرر لنموذج التفريغ، ثم يُحذف الملف دائماً (`finally`).
5. النص يُحفظ في `messages.text_content`، وتبقى `msg_type = 'audio'`.

**قرارات التفريغ:**

- **الملف المؤقت:**
  - مجلد `MEDIA_TMP_DIR`، والملف بصلاحية 0600.
  - الـ worker يحذف عند بدء تشغيله أي ملف أقدم من ساعة (بقايا توقف مفاجئ).
  - **لا نخزّن الصوت نفسه أبداً**، فقط النص. هذا منسجم مع حذف البيانات لـ Meta.
- **البوت ينتظر التفريغ:**
  - إذا كانت بين رسائل الزبون المعلّقة رسالة صوتية لم تُفرَّغ بعد، يؤجَّل الرد ثانيتين ويُعاد الفحص.
  - الحد الأقصى للانتظار `TRANSCRIPTION_MAX_WAIT_SECONDS` (45 ثانية).
  - بعد الحد يرد البوت على ما لديه، ويرى الوكيل `[رسالة صوتية]`.
  - انتهاء التفريغ يجعل الرد مستحقاً فوراً.
- **التلميحات (prompt hints):**
  - أسماء برامج الوكالة ومرادفاتها، ومدن الانطلاق، وأسماء الفنادق.
  - ثم قائمة ثابتة: مدن ليبية ومصطلحات الحج والعمرة (رباعي، ثلاثي، المولد...).
  - بحد أقصى في الطول، ومصطلحات الوكالة لها الأولوية.
- **النموذج:** `TRANSCRIPTION_MODEL`، والافتراضي `gpt-4o-transcribe`، واللغة `ar`. هو نفس مزوّد OpenAI، فلا مزوّد جديد.
- **الـ System prompt لم يتغير.** `history.py` يعرض النص للوكيل بصيغة `[رسالة صوتية: …]`، فيعرف أنه كلام مفرّغ قد يحتوي أخطاء.
- **الواجهة:**
  - تحديث `text_content` يرسل حدث `message` لحظياً (trigger جديد).
  - معاينة المحادثة في القائمة تُحدَّث إذا كانت هذه آخر رسالة.
- **الرد بالصوت (TTS):** مرفوض الآن، لأن جودة اللهجة الليبية غير مقنعة، والأسعار يجب أن تكون نصاً واضحاً.

### 3. البروشور إلى كتالوج مسودة

**الرفع:**

- `POST /api/v1/catalog/imports`، والملف هو **جسم الطلب نفسه** (raw body)، مع `Content-Type` من:
  - `image/jpeg`
  - `image/png`
  - `image/webp`
  - `application/pdf`
- **القرار:** لا نضيف مكتبة `python-multipart`. الواجهة ترسل الملف بـ `fetch(url, {body: file})`.
- **الصلاحية:** المدير أو المالك.
- **الفحوص:**
  - النوع يُتحقق منه من **أول بايتات الملف** (magic bytes)، وليس من الهيدر فقط.
  - الحد الأقصى `CATALOG_IMPORT_MAX_BYTES` (10MB).
  - نفس الملف (sha256) لنفس الوكالة لا يُعالج مرتين.
- **التخزين حتى المعالجة:**
  - الملف يُحفظ في `catalog_imports.file_data`، لأن الـ API والـ worker حاويتان منفصلتان بدون مجلد مشترك.
  - يُمسح بعد انتهاء المعالجة، نجاحاً أو فشلاً نهائياً.

**الاستخراج:**

- gpt-4o مع صورة (`image_url` بدقة `high`) أو PDF (جزء `file`).
- المخرجات عبر Structured Outputs (`json_schema` مع `strict`).
- **التعليمات:** استخرج المكتوب فقط ولا تخمّن. التواريخ الميلادية فقط إذا كانت مكتوبة. السعر مع عملته كما هي.

**التطبيع** يتم في Python (دالة بدون قاعدة بيانات، ومختبرة):

- **العملة:** الأسعار بالدينار الليبي فقط (القاعدة 3). أي سعر بعملة أخرى لا يُحفظ، ويظهر تحذير للمراجعة.
- **التواريخ:** تاريخ غير صالح أو عودة قبل الذهاب يُحذف موعده مع تحذير.
- **أنواع الغرف والمسافرين:** تُحوَّل للقيم المسموحة، وأي نوع غير معروف يُحذف مع تحذير.
- **تكرار السعر** لنفس الغرفة والمسافر والموعد: يبقى الأول مع تحذير.
- **الحدود:**
  - 20 برنامجاً للملف كحد أقصى.
  - أطوال النصوص.
  - المبالغ من صفر إلى أقل من مليون.

**النتيجة والاعتماد:**

- البرامج تُحفظ **`draft`**، مع `packages.source_import_id`.
- `search_packages` والوكيل لا يريان المسودات.
- **لا يستخدم البوت أي سعر مستخرج آلياً قبل اعتماد بشري.**
- **الاعتماد:** `POST /api/v1/catalog/packages/{id}/publish`.
  - يرفض برنامجاً بدون سعر للبالغين (`package_has_no_prices`).
  - يجدّد `updated_at` للأسعار، لأن الاعتماد تأكيد بشري للسعر، وهو ما يفحصه تنبيه «السعر قديم».
- **التصحيح قبل الاعتماد** (الحد الأدنى الضروري؛ التحرير الكامل في Agent Studio):
  - تعديل مبلغ سعر أو حذفه.
  - حذف موعد خاطئ.
  - حذف المسودة كاملة.

### 4. «احفظ ردك كمعلومة للبوت»

- **الاقتراح:** `GET /api/v1/conversations/{id}/knowledge-suggestion?message_id=`.
  - **الجواب:** كتلة ردود الموظف المتتالية التي فيها الرد المحدد (أو آخر رد للموظف)، لأن الموظف قد يجيب في أكثر من رسالة.
  - **السؤال:** آخر كتلة رسائل متتالية من الزبون قبل هذه الكتلة. رسائل البوت بينهما تُتخطى (مثل رسالة التحويل للموظف)، ونص الرسالة الصوتية المفرّغ يُستخدم كنص عادي.
  - يعيد `{question, answer, message_id}` ليعدّلها الموظف قبل الحفظ. `message_id` هو أول رسالة في كتلة الرد، فاختيار أي رسالة من نفس الكتلة يعطي نفس الاقتراح.
  - لا يوجد رد موظف مناسب: 404 `no_staff_answer`.
- **الحفظ:** `POST /api/v1/conversations/{id}/knowledge {question, answer, message_id?}`.
  - يُنشئ `knowledge_chunk` بنوع `faq`، مع `created_by` و `source_ref = conversation:<id>:message:<message_id>`.
  - **idempotent:** نفس الرد لا يُحفظ مرتين. الحفظ الأول 201 `{"embedding": "pending"}`، والتكرار 200 `{"duplicate": true}`.
- **الصلاحيات:**
  - أي موظف يستطيع الحفظ، لأنه من أجاب.
  - التعطيل (`DELETE /api/v1/knowledge/{id}`) للمدير فقط.
  - القائمة `GET /api/v1/knowledge` للجميع.
- **الـ embedding:**
  - يُحسب لاحقاً في الـ worker (مهمة `embed_knowledge`)، فلا يستدعي الـ API OpenAI، والحفظ لا يفشل إذا تعطّل OpenAI.
  - المعلومة متاحة **فوراً** للبحث النصي، لأن فرع FTS في البحث الهجين لا يحتاج embedding، ثم تدخل البحث الدلالي بعد الـ embedding.

## 7a: الملفات

| الجزء | الملفات (من `backend/`) |
|---|---|
| Migration | `migrations/versions/20260926_0012_ai_jobs_voice_catalog_import.py` |
| الطابور | `app/ai/jobs.py`، `app/ai/queries.py` (كل SQL المرحلة)، `app/worker/runner.py` (حلقتا `voice` و `ai`) |
| الصوت | `app/ai/transcription.py`، `app/channels/meta_graph.py` (تنزيل الوسائط)، `app/worker/ingest.py`، `app/worker/reply.py` (الانتظار) |
| البروشور | `app/ai/catalog.py` (الفحص، الـ schema، التطبيع، المسودات)، `app/api/v1/catalog.py` |
| المعرفة | `app/ai/knowledge.py`، `app/api/v1/knowledge.py` |
| OpenAI | `app/llm/base.py`، `app/llm/openai_client.py` (`OpenAITranscriptionClient`، `OpenAIDocumentExtractor`) |
| الاختبارات | `tests/unit/test_transcription.py`، `test_catalog_extraction.py`، `test_ai_jobs_knowledge.py`، `tests/api/test_catalog_knowledge_api.py`، `tests/sql/13–15` |
| التطوير | `scripts/dev_mock_upstream.py`، `scripts/dev_send_whatsapp.py --audio`، `scripts/dev_try_7a.sh` |

## 7a: API

كل الطلبات: كوكي الجلسة (أو `Bearer ses_…`) مع `X-Tenant-ID`، وطلبات الكتابة بالكوكي تحتاج `Origin` مسموحاً.

| الطلب | الصلاحية | ملاحظات |
|---|---|---|
| `POST /api/v1/catalog/imports?filename=` | مدير | الملف هو جسم الطلب. 202 جديد، 200 نفس الملف سابقاً. 413 / 415 / 422 بأسباب عربية |
| `GET /api/v1/catalog/imports` و `/imports/{id}` | مدير | الحالة `pending/processing/done/failed`، التحذيرات، والبرامج المستخرجة |
| `GET /api/v1/catalog/packages?status=&import_id=` و `/packages/{id}` | موظف | البرنامج مع المواعيد والأسعار |
| `POST /api/v1/catalog/packages/{id}/publish` | مدير | 409 `package_not_draft`، 422 `package_has_no_prices` |
| `DELETE /api/v1/catalog/packages/{id}` | مدير | المسودات فقط |
| `PATCH /api/v1/catalog/prices/{id}` `{amount?, notes?}` | مدير | مسودة فقط؛ غير ذلك 409 |
| `DELETE /api/v1/catalog/prices/{id}` و `/departures/{id}` | مدير | مسودة فقط؛ غير ذلك 409 |
| `GET /api/v1/conversations/{id}/knowledge-suggestion?message_id=` | موظف | 404 `no_staff_answer` |
| `POST /api/v1/conversations/{id}/knowledge` `{question, answer, message_id?}` | موظف | 201 جديد، 200 `duplicate` |
| `GET /api/v1/knowledge?source=all\|staff&include_inactive=` | موظف | |
| `DELETE /api/v1/knowledge/{id}` | مدير | تعطيل وليس حذفاً |

## 7a: ما تم التحقق منه فعلياً

| الاختبار | النتيجة |
|---|---|
| `pytest -q` | 232 ناجحة، منها 101 جديدة لـ 7a: `test_transcription` 36، `test_catalog_extraction` 21، `test_ai_jobs_knowledge` 15، `test_catalog_knowledge_api` 29 |
| `scripts/run_sql_tests.sh` على قاعدة فارغة بعد `alembic upgrade head` | كل الملفات 00–15 ناجحة. الجديد: Q1–Q2 (الطابور)، V1–V3 (الصوت و NOTIFY)، C1–C3 (البروشور والمسودات)، K1 (المعرفة)، S1 (الوكالة الموقوفة لا تُصرف عليها مهام) |
| Migration 0012 | `downgrade 0011` يزيل الجداول والدالة والعمود والـ trigger؛ `downgrade base` ثم `upgrade head` ناجحان |
| تشغيل حي: API + worker + Postgres + البديل المحلي | انظر التفاصيل أدناه |
| `scripts/dev_try_7a.sh` | ناجح (exit 0) من البداية للنهاية |
| `web/e2e/dashboard.e2e.js` (تأكد أن 7a لم تكسر اللوحة) | 40 فحصاً ناجحاً |

**التشغيل الحي:**

- **الصوت:**
  - النص حُفظ في الرسالة، ورد البوت بناءً عليه.
  - مع تأخير تفريغ 9 ثوانٍ: انتظر البوت، ثم ردّ بعد وصول النص بنحو ثانية.
  - وسائط منتهية (404 من Meta): فشلت المهمة نهائياً بدون إعادة، ورد البوت فوراً بدون انتظار الحد الأقصى.
  - مجلد الملفات المؤقتة بقي فارغاً بصلاحية 0700.
  - أسماء برامج الوكالة وفنادقها وصلت في الـ prompt.
- **البروشور:**
  - الفحوص: ملف فارغ 422، نوع غير مدعوم 415، حجم كبير 413، نوع لا يطابق المحتوى 415، رفع صحيح 202، نفس الملف مرة ثانية 200.
  - الاستخراج: مسودتان مع التحذيرات المتوقعة (سعر بالدولار لم يُحفظ، عملة غير مكتوبة اعتُبرت دينار، تاريخ عودة محسوب، موعد بدون تاريخ)، و `file_data` مُسح بعد المعالجة.
  - المسودة لا تظهر في `search_packages` حتى الاعتماد. التصحيح بعد الاعتماد مرفوض (409).
- **المعرفة:**
  - الاقتراح، والحفظ، ورفض التكرار، والظهور الفوري في البحث النصي.
  - الـ embedding من الـ worker، والتعطيل من المدير.

**خطأ اكتُشف في التشغيل الحي وأُصلح:** asyncpg يرفض نص التاريخ مع `CAST(:x AS date)`، فصارت الكتابة تمرر `date` و `Decimal` (commit منفصل مع اختبار).

## 7a: حدود معروفة

- **لم يُختبر مع Meta و OpenAI الحقيقيين.** كل التشغيل الحي كان عبر البديل المحلي بنفس العقود.
  - تنزيل الوسائط، وتفريغ `gpt-4o-transcribe`، وقراءة البروشور بـ gpt-4o لم تُجرَّب على خدمات حقيقية.
  - **جودة التفريغ باللهجة الليبية ودقة قراءة البروشور تحتاج تجربة على عينات حقيقية** قبل البيع (انظر «التجربة بالموديل الحقيقي»).
- **لا واجهة في اللوحة لـ 7a بعد:**
  - رفع البروشور ومراجعة المسودات واعتمادها وزر «احفظ ردك كمعلومة» كلها API فقط حالياً.
  - الرسالة الصوتية المفرّغة تظهر في المحادثة كنص عادي بدون علامة «صوتية».
- **صيغ صوت غير مدعومة:** AAC خام و AMR تفشل نهائياً، ويرد البوت بدون النص.
- **حالة استيراد البروشور** تُقرأ بالاستعلام الدوري؛ لا يوجد حدث لحظي لها.
- **التصحيح على المسودات فقط.** تعديل برنامج معتمد يبقى لـ Agent Studio.

## 7a: التجربة

### 1. تشغيل البيئة (مرة واحدة)

المتطلبات: Docker، و Python 3.11. من مجلد `backend/`:

```bash
cp .env.example .env
#   عبّئ: كلمات مرور الأدوار، TOKEN_ENCRYPTION_KEY، META_APP_SECRET، META_VERIFY_TOKEN، VOUCHER_PEPPER
#   للتطوير: COOKIE_SECURE=false   و   WEB_ALLOWED_ORIGINS='["http://localhost:3000"]'   و   WHATSAPP_ACCESS_TOKEN=أي-قيمة
docker compose up -d postgres
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
set -a; . ./.env; set +a
.venv/bin/alembic upgrade head                                  # حتى 0012

# البديل المحلي لـ Meta و OpenAI، ثم الـ API والإدارة والـ worker بنفس البيئة
.venv/bin/python -m scripts.dev_mock_upstream 8099 &
export META_GRAPH_BASE_URL=http://127.0.0.1:8099 OPENAI_BASE_URL=http://127.0.0.1:8099/v1 OPENAI_API_KEY=sk-mock
.venv/bin/uvicorn app.main:app --port 8000 &
.venv/bin/uvicorn app.admin.main:app --port 8001 &
.venv/bin/python -m app.worker.runner &

# خطة التسجيل الذاتي (مرة واحدة لكل قاعدة): create_admin يطبع توكن adm_...
.venv/bin/python -m scripts.create_admin --email ops@example.ly --name "مدير" --role superadmin
curl -X POST localhost:8001/admin/plans -H "Authorization: Bearer <adm_...>" -H 'Content-Type: application/json' \
     -d '{"code":"starter","name":"البداية","prices":[{"period_months":1,"price_lyd":"150"}]}'
```

### 2. التجربة الكاملة بأمر واحد

```bash
./scripts/dev_try_7a.sh                      # بروشور تجريبي مولَّد
./scripts/dev_try_7a.sh ~/brochure.jpg       # أو بروشورك: JPG / PNG / WEBP / PDF
```

السكربت ينشئ نشاطاً ورقماً جديدين في كل تشغيل، ثم:

1. يرسل رسالة صوتية، والتفريغ يتأخر 6 ثوانٍ عمداً، فيظهر أن البوت انتظر النص.
2. يرفع البروشور، ويعرض المسودات والتحذيرات، ويعتمد أول برنامج له أسعار.
3. يستلم المحادثة كموظف، ويرد، ثم يحفظ الرد كمعلومة وينتظر الـ embedding.

في النهاية يطبع حساب الدخول. افتحه في اللوحة (`http://localhost:3000`) لترى المحادثة والرسالة الصوتية المفرّغة.

### 3. خطوات منفردة بـ curl

```bash
API=http://127.0.0.1:8000; O='Origin: http://localhost:3000'
curl -c j.txt -X POST $API/api/v1/auth/login -H "$O" -H 'Content-Type: application/json' \
     -d '{"email":"<email>","password":"<password>"}'
T=$(curl -s -b j.txt $API/api/v1/me | python3 -c 'import sys,json; print(json.load(sys.stdin)["tenants"][0]["tenant_id"])')
H=(-b j.txt -H "X-Tenant-ID: $T" -H "$O")

# بروشور => مسودات
curl "${H[@]}" -X POST "$API/api/v1/catalog/imports?filename=brochure.jpg" -H 'Content-Type: image/jpeg' --data-binary @brochure.jpg
curl "${H[@]}" $API/api/v1/catalog/imports/<import_id>                  # الحالة + التحذيرات + البرامج
curl "${H[@]}" $API/api/v1/catalog/packages/<package_id>                # المواعيد والأسعار
curl "${H[@]}" -X PATCH $API/api/v1/catalog/prices/<price_id> -H 'Content-Type: application/json' -d '{"amount":"4600"}'
curl "${H[@]}" -X POST $API/api/v1/catalog/packages/<package_id>/publish

# رسالة صوتية من زبون (رقم القناة من register_whatsapp_channel)
.venv/bin/python -m scripts.dev_send_whatsapp --from 218913334444 --name "أبو محمد" --audio MOCKMEDIA-1 --phone-number-id <PNID>
curl -X POST localhost:8099/__mock/transcript -d '{"text":"قداش عمرة المولد؟","delay_seconds":0}'   # نص التفريغ الوهمي

# رد موظف => معلومة للبوت (بعد takeover والرد من اللوحة أو الـ API)
curl "${H[@]}" $API/api/v1/conversations/<conversation_id>/knowledge-suggestion
curl "${H[@]}" -X POST $API/api/v1/conversations/<conversation_id>/knowledge -H 'Content-Type: application/json' \
     -d '{"question":"هل تقبلوا التقسيط؟","answer":"نعم، على دفعتين."}'
curl "${H[@]}" "$API/api/v1/knowledge?source=staff"
```

### 4. التجربة بالموديل الحقيقي (بدون حساب Meta)

Meta يبقى على البديل المحلي، و OpenAI يصبح حقيقياً. البديل يعيد ملف صوتك عند تنزيل الوسائط.

```bash
# أوقف الـ API والـ worker، ثم في نفس الـ shell:
unset OPENAI_BASE_URL
read -rs OPENAI_API_KEY && export OPENAI_API_KEY        # الصق مفتاحك (لا يُكتب في الملفات ولا في history)
.venv/bin/uvicorn app.main:app --port 8000 &
.venv/bin/python -m app.worker.runner &

# رسالة صوتية حقيقية: ملاحظة صوتية من واتساب (.opus / .ogg) أو تسجيل .m4a / .mp3
curl -X POST localhost:8099/__mock/media -d '{"path":"/full/path/voice.ogg"}'
./scripts/dev_try_7a.sh ~/brochure.jpg                  # مع بروشور حقيقي
curl -X POST localhost:8099/__mock/media -d '{"path":null}'   # رجوع للصوت الوهمي
```

- هذا المسار لم يُجرَّب بعد على OpenAI الحقيقي (لا مفتاح في بيئة التطوير). التكلفة: تفريغ دقيقة صوت، واستدعاء gpt-4o بصورة، واستدعاء embedding واحد، وردود البوت.
- نص التفريغ الفعلي في رسالة الزبون (`GET /conversations/{id}/messages`) وفي اللوحة. الموديل والتوكنز والزمن لكل مهمة في جدول `ai_jobs`.

## 7b: الخطة (للتنفيذ بعد 7a)

1. **متابعة داخل نافذة 24 ساعة:**
   - رسالة بعد نحو 3 ساعات، وأخرى قبل انتهاء النافذة بساعتين أو ثلاث.
   - مرة واحدة لكل مرحلة، والمحادثة مع البوت، ولا طلب حجز.
   - تتوقف عند الرفض.
   - أوقات الهدوء: الليل، وصلاة الجمعة، والإفطار في رمضان.
   - **ندرة صادقة فقط:** «المقاعد قليلة» تُقال فقط إذا كان `seats_left` قليلاً فعلاً.
2. **الزبون الساخن:**
   - قواعد (البرنامج، التاريخ، عدد المسافرين، السؤال عن العربون أو الأوراق)، ثم أداة `flag_hot_lead(reason)`.
   - شارة حمراء في اللوحة.
   - تنبيه واتساب إذا لم يُتابع خلال مهلة.
3. **التقرير الصباحي على واتساب** (قالب Utility): المحادثات، والطلبات، والساخنة غير المتابعة، وما وصل خارج الدوام.
4. **قائمة إعداد من 3 خطوات، و«جرّب بوتك» داخل اللوحة.**
5. **التجربة المجانية:** 14 يوماً أو 100 محادثة، أيهما أقرب، مع تقرير قيمة في نهايتها.

## ما لا نبنيه (قرار)

- سحب منشورات فيسبوك وإنستغرام بدون الـ API الرسمي.
- الندرة المصطنعة.
- التسعير لكل Lead.
- الرد الصوتي الآن.

المزامنة الرسمية مع الصفحات ستأتي كإضافة مدفوعة، بعد صلاحيات Meta ومراجعتها، وبعد نجاح استخراج البروشور.
