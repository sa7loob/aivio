# حالة المشروع وخطة التسليم (2026-09-26)

## ما تم

| المرحلة | المحتوى | Migration |
|---|---|---|
| 1–2 | المعمارية، الوكالات، القنوات، الكتالوج (برامج، رحلات، أسعار)، الـ Leads، RLS | 0001–0004 |
| 3 | Webhooks Meta، worker (ingest ثم reply ثم send)، outbox، echo والاستلام البشري | 0002، 0005 |
| 4 | الـ Agent (gpt-4o + أدوات: search_packages، get_package_details، search_knowledge، create_lead، handoff_to_human)، RAG هجين، evals | 0006 |
| 4.5 | لوحة الإدارة الداخلية + المحفظة والاشتراكات والقسائم وسندات القبض | 0007–0008 |
| 5 / 5.1 | الهوية (تسجيل، دخول، جلسات، دعوات)، الربط الذاتي (WhatsApp Embedded Signup، صفحات فيسبوك، إنستغرام)، مسارات Meta الإلزامية، إعادة تعيين كلمة المرور | 0009–0010 |
| 6a | API اللوحة: Live Inbox، استلام الموظف، رد الموظف، Leads CRM، تصدير CSV، Realtime (SSE مع LISTEN/NOTIFY) | 0011 |
| **6b** | **واجهة اللوحة (Next.js، عربي RTL، جوال): المحادثات، طلبات الحجز، الفريق، القنوات** + أول تشغيل حقيقي للباكيند عبر HTTP | — |

التفاصيل: `docs/architecture/09-phase-6b-dashboard-ui.md`.

## ما تم التحقق منه فعلياً (هذه الجلسة)

البيئة: `docker compose` بصورة `pgvector/pgvector:pg17`، مع pgvector حقيقي، و Python 3.11، و FastAPI + asyncpg، و Node 22.

| الاختبار | النتيجة |
|---|---|
| `alembic upgrade head` ثم `downgrade base` ثم `upgrade head` | ناجح؛ بعد التراجع 0 جداول و0 دوال |
| `pytest -q` | 131 ناجحة، مع أو بدون `.env` محمّلاً في الـ shell |
| `scripts/run_sql_tests.sh` | كل الملفات 00–12 ناجحة |
| uvicorn + worker + curl | كل endpoints المرحلة 6a بمساراتها الناجحة والفاشلة، و SSE، ولا يوجد أي 5xx |
| `npm run typecheck` و`npm run build` | بدون أخطاء |
| `web/e2e/dashboard.e2e.js` | 40 فحصاً ناجحاً (حاسوب + جوال 390px + وضع داكن + نشاطان) |

إصلاحات الباكيند الناتجة (commits منفصلة):

- `run_sql_tests.sh`: مولّد PREPARE كان يفشل، فلم تكن اختبارات 11 و12 تعمل.
- `WEB_ALLOWED_ORIGINS` في `.env.example`: القيمة بين `'…'`.
- البحث برقم محلي في المحادثات والطلبات: `normalize_search`.
- `/connect` يختار النشاط من `?tenant=`.
- `conftest.py`: قيم الاختبار مفروضة.

## ما لم يُختبر بعد

- Meta الحقيقي (إرسال واتساب، Embedded Signup، Facebook Login) و OpenAI الحقيقي. في التجربة استُخدم بديل محلي بنفس العقود (`scripts/dev_mock_upstream.py`).
- nginx الإنتاجي: `proxy_buffering off` لمسار `/api/v1/events`. تم التحقق من البث عبر rewrites في Next فقط.

## ملاحظات مفتوحة

- **نسخة PostgreSQL:** `CLAUDE.md` يذكر 16، بينما `docker-compose.yml` و`01-architecture.md` يستخدمان 17. يُستحسن توحيد `CLAUDE.md`.
  - لا تغيّر صورة الـ compose على قاعدة قائمة، لأن الإصدار الرئيسي يحتاج `pg_upgrade`.
- **ملاحظات المتابعة** على الطلب لا تصل لحظياً للموظفين الآخرين الفاتحين نفس الطلب، لأن NOTIFY على `leads` فقط. تحتاج trigger على `lead_events` في migration لاحقة إن لزم.
- **`next build` يعدّل `web/next-env.d.ts`.** لا ترفع هذا التعديل، لأنه يكسر `typecheck` على نسخة جديدة.
- **تحذير Starlette** (`HTTP_422_UNPROCESSABLE_ENTITY`) غير مؤثر.

## ملاحظات تصميم الواجهة

- **الطلبات:**
  - كلها same-origin (`credentials: "same-origin"`) مع هيدر `X-Tenant-ID`.
  - EventSource ورابط تصدير CSV لا يرسلان هيدرات، فنمرر `?tenant=`. الباكيند يقبله لطلبات GET فقط.
- **Realtime:**
  - الحدث يحمل معرّفات فقط، والواجهة تعيد الجلب من الـ API بعد تأخير قصير.
  - التحديث الصامت يحافظ على عدد العناصر المحمّلة.
  - إذا فشل تحديث صامت تبقى البيانات المعروضة.
- **العرض:**
  - في RTL: رسائل الزبون على اليمين، وردودنا على اليسار.
  - الأرقام لاتينية، والتواريخ بتوقيت `Africa/Tripoli`.
- **الإنتاج:**
  - nginx يوجّه `/api` و `/connect` و `/meta` و `/webhooks` إلى FastAPI، والباقي إلى Next (`output: standalone`).
  - مسار `/api/v1/events` يحتاج `proxy_buffering off` و `proxy_read_timeout 1h`.

## المراحل التالية (من خارطة الطريق)

| المرحلة | المحتوى |
|---|---|
| 7 | Agent Studio: تعديل البرامج والأسعار وقاعدة المعرفة وشخصية البوت من اللوحة |
| 7 | الفوترة من جهة الوكالة: المحفظة، الاشتراك، شحن بالقسيمة، رفع إيصال تحويل |
| 8 | بوابة Plutu، قوالب واتساب (للرد بعد 24 ساعة والمتابعة)، التحليلات |
| لاحقاً | تطبيق الجوال، تيك توك |

قبل النشر الأول للوحة:

1. شغّل `web/e2e/dashboard.e2e.js` على بيئة staging.
2. جرّب ربط رقم واتساب حقيقي من `/connect`.
