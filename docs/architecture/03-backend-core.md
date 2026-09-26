# المرحلة 3: النواة الخلفية + شريحة واتساب

> الحالة: مكتملة (كود) — 2026-09-26. المسار: رسالة واتساب ← Webhook ← Worker ← رد تجريبي.

## المسار

1. `POST /webhooks/meta`: التحقق من التوقيع على الـ raw body، ثم INSERT في `webhook_events`، ثم 200. عند فشل قاعدة البيانات يُرجع 503 عمداً لكي تعيد Meta الإرسال.
2. Worker، حلقة **ingest**: يحجز الأحداث (`SKIP LOCKED` + lease)، يحللها عبر Parser حسب `object`، يحدد الوكالة عبر `resolve_channel_account`، ثم داخل `tenant_session` يعمل upsert للزبون، ثم upsert للمحادثة المفتوحة (ذرّي)، ثم يُدخل الرسالة مع `ON CONFLICT DO NOTHING`، ثم يؤجل `reply_due_at` (Debounce).
3. حلقة **reply**: `claim_due_conversations` ثم `FOR UPDATE` على المحادثة. إذا تغيّر `reply_due_at` (وصلت رسالة جديدة) يُترك الرد للدورة التالية. غير ذلك: يجمع الرسائل التي `handled_at IS NULL`، يبني الرد، يُدخله في `outbound_messages`، ثم يعلّم الرسائل كمُعالجة.
4. حلقة **send**: `claim_pending_outbound`، ثم قراءة في tx1، ثم طلب HTTP خارج أي transaction، ثم تسجيل النتيجة في tx2. الأخطاء المؤقتة (429/5xx/rate limit) تُعاد بـ exponential backoff، والدائمة (190، 131047، خارج نافذة 24 ساعة) تُسجَّل كـ `failed`.

## قرارات

- Migration 0005 أضافت `messages.handled_at` بدل الاعتماد على `last_outbound_at`، لتجنب فقدان رسالة تصل في transaction متزامنة.
- التوكنات مشفّرة بـ Fernet (`TOKEN_ENCRYPTION_KEY`)، وتُسجَّل عبر `scripts/register_whatsapp_channel.py` بدور `app_owner`.
- التسليم at-least-once: إذا حدث timeout بعد وصول الطلب إلى Meta فقد تتكرر الرسالة، وهذا نادر ومقبول في الـ MVP.
- كل SQL في `app/db/queries.py`، ويُستخدم `CAST(:x AS type)` وليس `:x::type`.
- Messenger وInstagram يُضافان كـ parser + sender في `channels/registry.py` فقط.

## التحقق (في بيئة العمل)

- 21 اختبار unit نجحت: التوقيع، الـ Parser، عميل واتساب مع MockTransport، الرد التجريبي.
- كل استعلامات الـ worker نُفّذت كـ prepared statements بدور `app_user` على Postgres مع RLS، ومرّ المسار كاملاً: ingest، ثم منع التكرار، ثم debounce، ثم reply، ثم send.
- لم يُشغَّل هنا: FastAPI وasyncpg وAlembic، لأن PyPI محجوب في بيئة العمل. `tests/api` تُشغَّل محلياً عند المطوّر.

## التالي

- اختبار E2E حقيقي على رقم الاختبار عبر tunnel.
- المرحلة 4: الـ Agent (أدوات `search_packages` و`create_lead`، والتقييم باللهجة الليبية).
