# المرحلة 1: المعمارية المرجعية (معتمدة)

> الحالة: **معتمدة كمرجع للمشروع** — 2026-09-26
> العميل التجريبي: وكالة سياحة وخدمات حج وعمرة (ليبيا) — B2B SaaS باشتراك شهري.

## 1. القرارات الثابتة

| البند | القرار |
|---|---|
| Backend | Python 3.11+ / FastAPI |
| Database | PostgreSQL 17 + pgvector + pg_trgm + Full-Text Search |
| Deployment | Docker Compose على VPS (Hetzner): `api`, `worker`, `postgres`, `caddy` |
| Architecture | Modular Monolith + Channel Adapters |
| القنوات (MVP) | Instagram Direct, Messenger, WhatsApp Business Cloud API — مصدرها Meta Webhooks |
| مستقبلاً | TikTok (Adapter جديد بنفس الـ Protocol) |
| مستبعد | Telegram |
| العزل | `tenant_id` في كل جدول + Row-Level Security عبر `app.tenant_id` |
| الطابور (MVP) | Postgres (`FOR UPDATE SKIP LOCKED`) — قابل للاستبدال بـ Redis/arq |

## 2. قيود Meta المؤثرة

- تطبيق Meta واحد للمنصة؛ التوجيه للوكالة من معرّف الحساب داخل الـ payload.
- App Review + Business Verification تبدأ بالتوازي مع التطوير (أعلى خطر زمني).
- نافذة 24 ساعة: خارجها Template (واتساب) أو Message Tag (ماسنجر/إنستغرام) ← نخزّن `last_inbound_at`.
- رقم الوكالة يعمل حالياً على تطبيق WhatsApp Business:
  - التطوير على رقم اختبار (Meta Test Number / SIM جديدة) دون المساس برقم الوكالة.
  - عند الإطلاق: Coexistence إن كان الحساب مؤهلاً، وإلا Inbox بسيط للموظف + Echo Detection للتدخل البشري.

## 3. Meta Webhook Adapter الموحد

- `GET /webhooks/meta`: التحقق (`hub.verify_token` ← إعادة `hub.challenge`).
- `POST /webhooks/meta`: التحقق من `X-Hub-Signature-256` (HMAC-SHA256 على الـ raw body بالـ App Secret).

| القناة | `object` | معرّف الحساب | معرّف الزبون | الرسائل |
|---|---|---|---|---|
| Messenger | `page` | `entry[].id` | `sender.id` (PSID) | `entry[].messaging[]` |
| Instagram | `instagram` | `entry[].id` | `sender.id` (IGSID) | `entry[].messaging[]` |
| WhatsApp | `whatsapp_business_account` | `value.metadata.phone_number_id` | `messages[].from` | `entry[].changes[].value.messages[]` |

كل Adapter يحوّل إلى `InboundMessage` موحّد (channel, account_external_id, user_external_id, external_message_id, type, text, timestamp, is_echo, raw) وينفّذ `parse / send_text / send_template`.

- أحداث الحالة (statuses/read/delivery) تُسجَّل ولا تصل للـ Agent.
- رسائل Echo من Business Suite ← إيقاف البوت مؤقتاً في المحادثة (Human Handoff).

## 4. مسار الرسالة

```
Meta ─POST─► /webhooks/meta
  1. raw body + توقيع (401 إن فشل)
  2. INSERT webhook_events (pending)
  3. return 200   ← لا LLM ولا Graph API هنا
Worker:
  4. claim: FOR UPDATE SKIP LOCKED
  5. Adapter.parse ← InboundMessage[]
  6. resolve_channel_account ← tenant_id
  7. SET LOCAL app.tenant_id ; upsert contact + conversation
  8. INSERT message ON CONFLICT DO NOTHING (منع التكرار)
  9. Debounce (~4 ث) عبر conversations.reply_due_at
 10. قفل على المحادثة
 11. Agent (أدوات + LLM)
 12. outbound_messages ← إرسال (مع فحص نافذة 24 ساعة وإعادة المحاولة)
 13. create_lead ← إشعار المبيعات (قالب واتساب Utility + لوحة التحكم)
```

- لا `BackgroundTasks` (تضيع الرسائل عند إعادة التشغيل).

## 5. نموذج البيانات (ملخص)

- المنصة والقنوات: `tenants, staff_users, channel_accounts, contacts, conversations, messages, webhook_events, outbound_messages`.
- الكتالوج: `packages, package_aliases, package_hotels, package_departures, package_prices, knowledge_chunks`.
- المبيعات: `leads, lead_events`.
- السعر حسب نوع الغرفة (رباعي/ثلاثي/ثنائي/مفرد) وفئة المسافر، والبرنامج له عدة مواعيد انطلاق.
- الـ Agent لا يكتب SQL؛ يستخدم أدوات: `search_packages`, `get_package_details`, `create_lead`.
- لا جوازات سفر داخل المحادثة في الـ MVP.

## 6. خريطة الطريق

0 التحديد ✅ · 1 المعمارية ✅ · 2 قاعدة البيانات والـ Migrations · 3 النواة الخلفية (Webhook + Worker + Adapters) · 4 الـ Agent (أدوات + RAG + تقييم باللهجة الليبية) · 5 Inbox/لوحة الوكالة · 6 تجربة ميدانية · 7 التوسع والفوترة
