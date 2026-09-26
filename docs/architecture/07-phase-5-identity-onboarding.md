# المرحلة 5: الهوية + الربط الذاتي للقنوات + ماسنجر/إنستغرام

> الحالة: منفذة (كود + اختبارات قاعدة بيانات ووحدات) — 2026-09-26. تحديث 5.1: مسارا Meta الإلزاميان + إعادة تعيين كلمة المرور.
> لم يُختبر هنا: التشغيل عبر FastAPI/asyncpg، والربط الحقيقي مع Meta (يتطلب تطبيق Meta معتمداً كـ Tech Provider).

## الهدف

أن يسجّل صاحب النشاط حسابه بنفسه، ويربط واتساب وماسنجر وإنستغرام من المتصفح بدون تدخل منا، وأن يرد البوت على ماسنجر وإنستغرام، وأن يتوقف تلقائياً عندما يرد موظف من خارج النظام.

## ما تم تنفيذه

| الجزء | الملفات |
|---|---|
| Migration 0009 | `migrations/versions/20260926_0009_identity_onboarding.py` |
| الهوية والجلسات | `app/identity/security.py`، `app/identity/queries.py`، `app/api/deps.py`، `app/api/v1/auth.py` |
| الربط الذاتي | `app/onboarding/service.py`، `app/api/v1/onboarding.py`، `app/channels/meta_graph.py` |
| ماسنجر/إنستغرام | `app/channels/messenger.py`، `app/channels/registry.py`، `app/worker/sender.py` |
| echo والاستلام البشري | `app/channels/whatsapp.py` (smb_message_echoes)، `app/worker/ingest.py` |
| مراقبة التوكنات | `app/worker/health.py`، `app/worker/runner.py` |
| صفحة الربط المؤقتة | `app/web/connect.html` على `/connect` (حتى لوحة التحكم في المرحلة 6) |
| لوحة الإدارة | `POST /admin/tenants/{id}/owner-invitation` لربط المكاتب المسجلة يدوياً بحساب دخول |

## قاعدة البيانات (0009)

- `users`، `user_sessions`، `auth_attempts`: جداول عامة (غير تابعة لوكالة). الجلسة تُخزن hash التوكن فقط.
- `memberships (tenant_id, user_id, role owner|admin|agent)`: عليها RLS بسياسة مزدوجة، تُرى صفوف الوكالة الحالية أو عضوياتك أنت (`app.user_id`). لا يوجد INSERT مباشر لدور التشغيل.
- `invitations`: عليها RLS، والتوكن مخزن كـ hash، وأدوارها owner (من لوحة الإدارة فقط) أو admin أو agent.
- `onboarding_sessions`: عليها RLS، وهي آلة حالات الربط، وتحفظ التوكن المؤقت مشفّراً.
- `staff_users.user_id`: يربط مستقبل إشعارات الـ Leads بحساب الدخول.
- دوال `SECURITY DEFINER`:

| الدالة | الغرض | الضمانة |
|---|---|---|
| `register_tenant()` | إنشاء الوكالة والعضوية والموظف والاشتراك التجريبي | تتطلب `app.user_id` |
| `accept_invitation()` | قبول دعوة | يربط الموظف الموجود بنفس البريد/الهاتف بدل تكراره |
| `my_tenants()` | وكالات المستخدم | قبل اختيار وكالة |
| `upsert_channel_account()` | حجز قناة للوكالة الحالية | يعيد `conflict` بدون أي تفاصيل إذا كانت القناة لوكالة أخرى |
| `channels_due_for_health_check()` | حجز قنوات للفحص | lease عبر `last_checked_at` |

## API (`/api/v1`)

| Endpoint | الصلاحية | ملاحظات |
|---|---|---|
| `POST /auth/register` | عام | مستخدم + نشاط + تجربة. مع `invite_token` ينضم لنشاط قائم بدل إنشاء نشاط |
| `POST /auth/login`، `/auth/logout`، `/auth/logout-all`، `/auth/password` | — | حد: 10 فشل لكل بريد و30 لكل IP خلال 15 دقيقة |
| `GET /me` | مستخدم | الحساب ووكالاته |
| `GET /team/members`، `POST/GET/DELETE /team/invitations` | admin+ | admin لا يدعو admin، المالك فقط |
| `POST /team/invitations/accept` | مستخدم | — |
| `POST /onboarding/whatsapp/start`، `/onboarding/whatsapp/{id}/complete` | admin+ | Embedded Signup، مع دعم Coexistence |
| `POST /onboarding/facebook/start`، `/{id}/complete`، `/{id}/connect` | admin+ | صفحات فيسبوك + إنستغرام المرتبط |
| `POST /onboarding/{id}/retry`، `GET /onboarding/{id}` | admin+ | استئناف بعد فشل خطوة لاحقة للتوكن |
| `GET /channels`، `POST /channels/{id}/disconnect` | member / admin+ | — |

**الوكالة في الطلب:** هيدر `X-Tenant-ID`، أو الوكالة الوحيدة للمستخدم. العضوية تُتحقق في كل طلب، ثم `tenant_session(tenant, user)` فتطبق RLS.

## قرارات معمارية

1. **جلسات opaque بدل JWT** (تغيير عن وثيقة 05): إلغاء فوري للجلسة، بلا مكتبة JWT، وبنفس نمط توكنات الإدارة. الويب يستخدم كوكي httpOnly + SameSite=Lax + فحص Origin للطلبات المغيّرة. الجوال يستخدم `Authorization: Bearer` مع `X-Auth-Mode: token`.
2. **scrypt من المكتبة القياسية** لكلمات المرور: بدون اعتماد جديد، والصيغة تسمح برفع التكلفة لاحقاً (`needs_rehash`).
3. **أدوار العضوية** owner/admin/agent (ليست أدوار قاعدة بيانات): ضرورية لتعدد المستخدمين داخل الوكالة، وأدوار قاعدة البيانات الثلاثة لم تتغير.
4. **Embedded Signup قابل للاستئناف:**
   - الـ code يُستبدل أولاً، والتوكن يُحفظ مشفّراً قبل أي خطوة أخرى.
   - الحجز (`step=exchanging`) يتم داخل قفل الصف، فلا يُستبدل نفس الـ code مرتين عند الإرسال المتزامن.
   - PIN التسجيل يُحفظ قبل الطلب، فإعادة المحاولة تستخدم نفس الـ PIN.
5. **لا transaction مفتوحة أثناء طلبات Meta**، وهو نفس مبدأ الـ sender.
6. **الاستلام البشري التلقائي عبر echo:**
   - ماسنجر/إنستغرام: `is_echo`.
   - واتساب Coexistence: `smb_message_echoes`.
   - رسالة من الموظف (ليست من البوت) تُسجل كرسالة `staff`، وتوقف البوت `AGENT_HANDOFF_PAUSE_HOURS`، وتعتبر رسائل الزبون المعلّقة مُجابة.
   - تمييز رد البوت نفسه يتم عبر: `metadata` (ماسنجر)، ثم `app_id`، ثم مطابقة معرّف الرسالة، ثم إرسال حديث لنفس الزبون خلال 60 ثانية.
7. **فصل القناة لا يلغي اشتراك الـ webhook عند Meta:** صفحة فيسبوك قد تخدم ماسنجر وإنستغرام معاً. الأحداث تُتجاهل لأن القناة غير نشطة.
8. **إنستغرام المربوط بصفحة يُرسل عبر `/{page_id}/messages`** بتوكن الصفحة (`config.page_id`).

## الأمان

- لا يمكن لوكالة رؤية أو حجز قناة وكالة أخرى (اختبار I5)، ولا إضافة عضوية مباشرة (I3).
- التوكنات (Meta، الجلسات، الدعوات، state) لا تُخزن نصاً صريحاً، وتوكنات الصفحات لا تعود للواجهة.
- `state` لكل جلسة ربط يُقارن بزمن ثابت، وصالح 20 دقيقة (24 ساعة بعد الحصول على التوكن للاستئناف).
- صفحة `/connect` بـ `X-Frame-Options: DENY` و`Cache-Control: no-store`، وتزيل توكن الدعوة من شريط العنوان.

## الاختبارات

- **قاعدة البيانات** (`tests/sql/08_identity_as_app_user.sql`، 7 اختبارات بدور `app_user`): التسجيل، شروطه، عزل العضويات، الدعوات، حجز القنوات بدون تسريب، lease الفحص، ربط موظف مكتب مسجل يدوياً.
- **الوحدات:**
  - `test_messenger.py`: parsers، echo، المرسل، الأخطاء.
  - `test_identity_security.py`.
  - `test_onboarding_service.py`: الحالة السعيدة، state خاطئ، فشل الـ code، الاستئناف بنفس الـ PIN، Coexistence، التعارض، الصفحات + إنستغرام.
  - `test_channel_health.py`.
- كل SQL الجديد جُهّز كـ prepared statements بدور `app_user`، ونُفّذ سيناريو كامل: تسجيل، ثم جلسة، ثم عضوية، ثم دعوة، ثم جلسة ربط، ثم قناة، ثم echo واستلام بشري، ثم إلغاء الجلسة.
- صفحة `/connect` عُرضت في Chromium بعرض 390px (RTL، رسالة خطأ، قائمة القنوات).

## قيود معروفة

- **لا يعمل الربط الذاتي فعلياً قبل** اعتماد التطبيق كـ Tech Provider، وموافقة App Review على الصلاحيات، وإنشاء إعدادات Embedded Signup وFacebook Login في لوحة Meta.
- **لا يوجد بعد تأكيد البريد:** إعادة تعيين كلمة المرور تتم برابط يصدره الدعم. إرسال تلقائي عبر بريد أو قالب واتساب Authentication يحتاج قراراً بالمزوّد.
- **Embedded Signup لا يسجّل `meta_user_id`**، لأن توكن الأعمال يخص System User. Data Deletion يغطي قنوات Facebook Login فقط، وقنوات واتساب تُفصل من لوحة المكتب أو الإدارة.
- **إنستغرام بدون صفحة فيسبوك** (Instagram Login) غير مدعوم بعد.
- **عرض المرسل في الـ echo:** رسائل الموظف تظهر `staff` بدون تحديد أي موظف (Meta لا ترسل هوية المرسل).
- **الإرسال خارج نافذة 24 ساعة على ماسنجر** (HUMAN_AGENT tag) غير مدعوم.

## التحديث 5.1 (Migration 0010)

| الجزء | التفاصيل |
|---|---|
| `POST /meta/deauthorize` | `signed_request` موقّع بسر التطبيق => القنوات التي ربطها هذا المستخدم تصبح `needs_reauth` |
| `POST /meta/data-deletion` | حذف التوكنات وفصل القنوات المرتبطة به، ويعيد `{url, confirmation_code}` كما تطلب Meta |
| `GET /meta/data-deletion/{code}` | صفحة حالة عربية/إنجليزية لا تكشف إلا الحالة والتاريخ |
| ربط القنوات بمستخدم فيسبوك | مسار Facebook Login يحفظ `meta_user_id` في إعداد القناة، ولا يظهر للواجهة |
| `POST /admin/users/password-reset` | الدعم يصدر رابطاً لمرة واحدة (24 ساعة) ويرسله لصاحب الحساب عبر واتساب |
| `POST /api/v1/auth/password-reset` | يستهلك الرابط، ويغيّر كلمة المرور، ويلغي كل الجلسات. المحاولة الفاشلة تُحسب في حد المحاولات |
| صفحة `/connect?reset=...` | نموذج كلمة المرور الجديدة، والتوكن يُزال من شريط العنوان |

قاعدة البيانات:
- `meta_data_deletion_requests` و`password_reset_tokens` لا يصل لهما دور التشغيل إلا عبر ثلاث دوال: `meta_user_revoked`، `meta_deletion_status`، `consume_password_reset`.
- رمز التأكيد عشوائي من `gen_random_uuid()` (بدون pgcrypto).

الاختبارات:
- **قاعدة البيانات:** M1 وM2 (عزل الأثر على قنوات المستخدم المعني فقط، وحذف التوكن) وR1 (استخدام واحد، الانتهاء، إلغاء الجلسات).
- **الوحدات:** `signed_request` (توقيع صحيح، تزوير، سر خاطئ، خوارزمية خاطئة، مدخلات تالفة)، وتسجيل `meta_user_id` وإخفاؤه.

## الخطوة التالية

- قبل App Review: تسجيل روابط `/meta/deauthorize` و`/meta/data-deletion` في إعدادات التطبيق، وسياسة الخصوصية، وفيديوهات الصلاحيات.
- المرحلة 6: لوحة التحكم (Next.js)، وتستهلك هذه الـ APIs مباشرة. صفحة `/connect` تُستبدل بشاشة Channels Hub.
