# المرحلة 6a: API لوحة التحكم (Live Inbox + الاستلام البشري + Leads CRM + Realtime)

> الحالة: الباكيند منفّذ ومختبر على مستوى قاعدة البيانات والمنطق — 2026-09-26.
> لم يُختبر هنا: تشغيل الـ endpoints عبر FastAPI و asyncpg (غير متاحين في بيئة التطوير هذه). الواجهة (Next.js) هي المرحلة 6b.

## الهدف

موظف الوكالة يفتح اللوحة ويرى المحادثات لحظياً، ويستلم أي محادثة من البوت ويرد بنفسه، ثم يرجعها للبوت.
ويتابع طلبات الحجز (Leads): يغيّر حالتها، ويسندها لموظف، ويكتب ملاحظات، ويصدّرها Excel.

## الملفات

| الجزء | الملفات |
|---|---|
| Migration 0011 | `migrations/versions/20260926_0011_inbox_realtime.py` |
| الاستعلامات | `app/dashboard/queries.py` |
| منطق بدون قاعدة بيانات | `app/dashboard/logic.py` (cursor، نافذة 24 ساعة، تغييرات الـ Lead، CSV) |
| Inbox | `app/api/v1/inbox.py` |
| Leads | `app/api/v1/leads.py` |
| Realtime | `app/realtime/hub.py`، `app/realtime/sse.py`، `app/api/v1/events.py` |
| البوت لا يرد بعد الاستلام | `app/worker/reply.py` (`should_discard`) |
| echo رد الموظف | `app/db/queries.py` (`RECENT_BOT_SEND_TO` يشمل `staff_reply`) |
| وكالة عبر query لـ SSE | `app/api/deps.py` (`?tenant=` للطلبات GET فقط) |

## قاعدة البيانات (0011)

- `conversations`: `assigned_user_id`، `takeover_by`، `takeover_at`، `last_message_at`، `last_message_preview`، `last_message_direction`، `unread_count`.
- trigger `messages_update_conversation`: يحدّث ملخص المحادثة وعدد غير المقروء عند كل رسالة، مهما كان مصدرها (زبون، أو بوت، أو موظف، أو echo من تطبيق الهاتف).
- `messages.client_msg_id`: عليه فهرس unique داخل الوكالة، ويضمن أن إعادة إرسال نفس الطلب من الواجهة لا تكرر الرسالة.
- `outbound_messages.purpose` صار يشمل `staff_reply`: رد الموظف منفصل عن `reply` الخاص بالبوت، فالاستلام يلغي ردود البوت المعلّقة فقط.
- `agent_runs.status` صار يشمل `discarded`.
- NOTIFY على قناة `tenant_events` يحمل `{t, type, id, conversation_id}` فقط ولا يحمل بيانات زبائن. الأنواع هي:
  - `message`
  - `conversation`: فقط عند تغيّر `mode` أو `assigned_user_id` أو `bot_paused_until` أو `unread_count`، وليس عند حقول الـ worker.
  - `lead`
  - `message_status`
- فهارس keyset للقوائم: `conversations_inbox_idx`، `messages_conversation_page_idx`، `leads_tenant_created_idx`.
- الـ downgrade كامل. عند الترقية يُعبّأ ملخص آخر رسالة للمحادثات الموجودة، ويبدأ عدد غير المقروء من صفر.

## قاعدة التزامن بين الموظف والبوت

1. الاستلام (`takeover`) أو أول رد من الموظف يتم تحت قفل صف المحادثة (`FOR UPDATE`). نتيجته:
   - `mode='human'`
   - `reply_due_at=NULL`
   - إلغاء ردود البوت المعلّقة (`status='pending'`)
   - إسناد المحادثة للموظف إن لم تكن مسندة لأحد
2. الـ worker لا يأخذ محادثة `mode<>'bot'` (سلوك موجود من المرحلة 3).
3. إذا كان الـ Agent يعمل لحظة الاستلام: `_persist` يأخذ نفس القفل ويعيد الفحص. عندها:
   - لا يُحفظ الرد ولا يُرسل.
   - يُسجَّل `agent_run` بحالة `discarded`.
   - رسائل الزبون تبقى بدون `handled_at`.
4. نفس الإهمال يحدث إذا رد صاحب الوكالة من تطبيق واتساب للأعمال أثناء التشغيل (echo يوقف البوت مؤقتاً). أما `handoff_to_human` من البوت نفسه فليس استلاماً، ورسالة التحويل تُرسل.
5. `release` يرجع المحادثة للبوت. إن بقيت رسائل زبون بدون رد، يصير `reply_due_at=now()` ويرد عليها البوت فوراً.
6. `close` يغلق المحادثة، وأي رسالة جديدة من الزبون تفتح محادثة جديدة (سلوك ingest الحالي).

## الـ Endpoints

كل الطلبات تمر بجلسة الدخول ثم `X-Tenant-ID` ثم فحص العضوية ثم `tenant_session`، ويبقى RLS خط الدفاع الأخير.

| Endpoint | الصلاحية | ملاحظات |
|---|---|---|
| `GET /api/v1/conversations` | agent+ | `view`، `channel`، `assigned=me/unassigned/any`، `q` (اسم أو رقم)، `cursor`، `limit≤100` |
| `GET /api/v1/conversations/{id}` | agent+ | يشمل `reply_window_open` |
| `GET /api/v1/conversations/{id}/messages` | agent+ | الأقدم أولاً، و`older_cursor` للصفحة الأقدم، ومع كل رسالة `delivery_status` |
| `POST …/read`، `…/takeover`، `…/release`، `…/close` | agent+ | المحادثة المغلقة ترفض takeover و release بخطأ 409 |
| `POST …/assign {user_id}` | agent لنفسه، admin+ لغيره | المستخدم المُسند إليه يجب أن يكون عضواً نشطاً، وإلا 422 `user_not_member` |
| `POST …/messages {text, client_msg_id}` | agent+ | يستلم المحادثة تلقائياً. الأخطاء المحتملة: `reply_window_closed`، `channel_not_active`، `conversation_closed`. الطلب المكرر يعيد `duplicate: true` |
| `POST /api/v1/messages/{id}/retry` | agent+ | لرد موظف فاشل فقط |
| `GET /api/v1/team/staff` | agent+ | لاختيار الموظف في الإسناد |
| `GET /api/v1/leads` | agent+ | `status`، `assigned=any/unassigned/<staff_id>`، `package_id`، `q`، `from_date`، `to_date`، `cursor` |
| `GET /api/v1/leads/{id}` | agent+ | مع سجل الأحداث واسم الموظف الفاعل |
| `PATCH /api/v1/leads/{id}` | agent+ | الحالة، والإسناد (`assigned_to` أو `unassign`)، والملاحظات. `lost` تتطلب `lost_reason`. كل تغيير له حدث في `lead_events`. أول تغيير للحالة يسجّل `first_contact_at` |
| `POST /api/v1/leads/{id}/notes` | agent+ | ملاحظة متابعة في السجل |
| `GET /api/v1/leads/export.csv` | admin+ | عناوين عربية، و BOM لـ Excel، وتوقيت الوكالة، وأرقام محمية بعلامة LRM، وحماية من formula injection، وحد 10,000 صف |
| `GET /api/v1/events?tenant=` | agent+ | SSE: `ready`، ثم أحداث بالمعرّفات، ونبضة كل 20 ثانية، وإغلاق بعد 30 دقيقة ليُعاد فحص الجلسة |

### Realtime

- `EventHub` يفتح اتصال asyncpg واحداً لكل عملية API، ويستمع بـ `LISTEN tenant_events`، ثم يوزّع الأحداث على مشتركي نفس الوكالة فقط، ولا يُرسل `tenant_id` للمتصفح.
- طابور كل متصفح محدود بـ 200 حدث. إذا امتلأ يُفرَّغ ويُرسل حدث `resync` واحد، فتعيد الواجهة تحميل القائمة.
- عند انقطاع قاعدة البيانات يعيد الاتصال مع backoff (1، 2، 5، 10، 30 ثانية) ثم يرسل `resync` للجميع.
- إعداد nginx لمسار `/api/v1/events`:

```nginx
proxy_buffering off;
proxy_read_timeout 1h;
proxy_http_version 1.1;
proxy_set_header Connection "";
```

## الاختبارات التي شُغّلت فعلياً

- **سلسلة migrations جديدة 0001→0011** على PostgreSQL 16، ثم مجموعة اختبارات SQL كاملة، ثم downgrade كامل انتهى إلى 0 جداول و0 دوال.
- **roundtrip للـ migration 0011** (down ثم up) على قاعدة فيها بيانات.
- **`tests/sql/11_inbox_leads_as_app_user.sql` (N1–N11)** بدور app_user، على نصوص الاستعلامات الحقيقية المولّدة كـ PREPARE عبر `scripts/gen_prepared_sql.py`:
  - الملخص وغير المقروء.
  - الفلاتر والبحث و keyset.
  - الاستلام وإلغاء ردود البوت المعلّقة، وتجاوز الـ worker للمحادثة.
  - رد الموظف و idempotency.
  - صفحات الرسائل مع حالة التسليم.
  - إعادة الإرسال.
  - الإسناد للأعضاء فقط.
  - الإرجاع للبوت.
  - التشغيل المُهمل والإغلاق.
  - Leads: الفلاتر والتحديث والأحداث والتصدير.
  - عزل الوكالات قراءةً وكتابة.
- **`tests/sql/12_realtime_notify_as_app_user.sql` (T1–T3)**:
  - إشعار لكل نوع حدث.
  - تحديث حقول الـ worker لا يرسل إشعاراً.
  - الإشعار لا يحتوي نصوص ولا أرقام.
- **PREPARE لـ 97 استعلاماً** (db، dashboard، identity) بدور app_user.
- **Unit tests: 114 ناجحة**، والجديد منها:
  - `test_dashboard_logic`
  - `test_realtime` (عزل الوكالات، و resync للمتصفح البطيء، وإعادة الاتصال، ومولّد SSE)
  - `test_reply_discard`

## القيود والملاحظات

- الـ endpoints نفسها لم تُشغّل عبر HTTP هنا لأن FastAPI و asyncpg غير متاحين في بيئة التطوير. المطلوب على جهازك: `pytest` ثم `uvicorn` ثم تجربة `curl` من README.
- نافذة الـ 24 ساعة تُفحص في الـ API وعند الإرسال. الرد بعد انتهائها يحتاج قوالب واتساب، وهي مرحلة لاحقة.
- عدد غير المقروء واحد على مستوى المحادثة، وليس لكل موظف.
- التصدير CSV وليس xlsx: يفتح في Excel بالعربية مباشرة، ولا يحتاج مكتبة إضافية.
