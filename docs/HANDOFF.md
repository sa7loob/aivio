# حالة المشروع وخطة التسليم (2026-09-26)

## ما تم (الباكيند)

| المرحلة | المحتوى | Migration |
|---|---|---|
| 1–2 | المعمارية، الوكالات، القنوات، الكتالوج (برامج، رحلات، أسعار)، الـ Leads، RLS | 0001–0004 |
| 3 | Webhooks Meta، worker (ingest ثم reply ثم send)، outbox، echo والاستلام البشري | 0002، 0005 |
| 4 | الـ Agent (gpt-4o + أدوات: search_packages، get_package_details، search_knowledge، create_lead، handoff_to_human)، RAG هجين، evals | 0006 |
| 4.5 | لوحة الإدارة الداخلية + المحفظة والاشتراكات والقسائم وسندات القبض | 0007–0008 |
| 5 / 5.1 | الهوية (تسجيل، دخول، جلسات، دعوات)، الربط الذاتي (WhatsApp Embedded Signup، صفحات فيسبوك، إنستغرام)، مسارات Meta الإلزامية، إعادة تعيين كلمة المرور | 0009–0010 |
| 6a | API اللوحة: Live Inbox، استلام الموظف، رد الموظف، Leads CRM، تصدير CSV، Realtime (SSE مع LISTEN/NOTIFY) | 0011 |

### ما تم التحقق منه في بيئة التطوير السابقة

- **قاعدة البيانات:** migrations من 0001 إلى 0011 على PostgreSQL 16 (بدون pgvector)، وكل اختبارات SQL، والتراجع الكامل.
- **Unit tests:** 114 اختبار عبر مشغّل بديل.

### ما لم يُشغَّل هناك (أول مهمة لك)

- FastAPI و asyncpg الحقيقيان: الـ endpoints لم تُطلب عبر HTTP قط.
- pgvector الحقيقي.
- `pytest` الرسمي (منه `tests/api/test_webhooks.py`).

## ما هو قيد التنفيذ: المرحلة 6b (واجهة Next.js في `web/`)

### مكتوب (لم يُبنَ أو يُختبر بعد)

| المجموعة | الملفات |
|---|---|
| الإعداد | `package.json`، `tsconfig.json`، `next.config.mjs` (فيه rewrites لـ `/api` و `/connect`، و `compress: false` من أجل SSE) |
| مكتبات مشتركة | `lib/api.ts` (عميل + رسائل خطأ عربية)، `lib/session.tsx` (المستخدم + النشاط الحالي + الأدوار)، `lib/events.tsx` (اتصال SSE واحد + `useLiveEvents`)، `lib/format.ts`، `lib/types.ts` |
| التصميم | `app/globals.css` (RTL + فاتح/داكن + جوال)، `components/ui.tsx`، `components/icons.tsx`، `components/AuthCard.tsx` |
| الصفحات | `app/login`، `app/register` (مع `?invite=`)، `app/reset` |
| هيكل اللوحة | `app/(app)/layout.tsx` (sidebar، مؤشر الاتصال اللحظي، تبديل النشاط) |
| صندوق المحادثات | `components/inbox/ConversationList.tsx`، `components/inbox/Thread.tsx` (استلام، إرجاع، إغلاق، إسناد، إرسال مع client_msg_id، رسائل أقدم، حالة التسليم، إعادة المحاولة) |

### المتبقي لإكمال 6b

1. `app/(app)/inbox/page.tsx`:
   - يجمع `ConversationList` و `Thread`.
   - الفلاتر في state، والمحادثة المختارة في `?c=`.
   - تحديث القائمة debounced عند أحداث `message` و `conversation` و `resync`.
   - "تحميل المزيد" بـ `next_cursor`.
   - يستخدم `Suspense` لأن الصفحة تستخدم `useSearchParams`.
2. `app/(app)/leads/page.tsx`:
   - جدول بفلاتر: الحالة، الموظف (`any` أو `unassigned` أو staff_id)، البحث، من/إلى تاريخ، "تحميل المزيد".
   - Drawer للتفاصيل، فيه:
     - تغيير الحالة، مع `lost_reason` إلزامي عند "لم يتم".
     - الإسناد (بـ staff id من `/api/v1/team/staff`).
     - الملاحظات، وإضافة ملاحظة للسجل.
     - Timeline الأحداث.
     - رابط للمحادثة `/inbox?c=`.
   - زر تصدير CSV (admin+) بالرابط `/api/v1/leads/export.csv?tenant=<id>&...`.
3. `app/(app)/team/page.tsx`:
   - الأعضاء (`/api/v1/team/members`، admin+). الموظف العادي يرى رسالة "للمدير فقط".
   - الدعوات: إنشاء (role و note)، ثم عرض الرابط `${origin}/register?invite=<token>` مرة واحدة، مع زر نسخ ورابط مشاركة واتساب. عرض الدعوات وإلغاؤها.
4. `app/(app)/channels/page.tsx`:
   - قائمة القنوات وحالتها (`/api/v1/channels`).
   - زر "ربط قناة" يفتح `/connect`، وهي صفحة الباكيند الجاهزة لتدفقات Meta.
   - زر فصل القناة (admin+).
   - رسالة ترحيب عند `?welcome=1`.
5. التحقق:
   - `npm run typecheck && npm run build` بدون أخطاء.
   - تشغيل فعلي مع الباكيند.
   - تجربة: تسجيل ← المحادثات ← الاستلام ← الرد ← الإرجاع ← Leads ← التصدير ← الفريق ← القنوات.
   - تجربة الجوال (عرض 390px).
6. التوثيق: `docs/architecture/09-phase-6b-dashboard-ui.md`، وتحديث README.

### ملاحظات تصميم مهمة للواجهة

- كل الطلبات same-origin (`credentials: "same-origin"`) مع هيدر `X-Tenant-ID`.
- EventSource لا يرسل هيدرات، لذلك نمرر `?tenant=`. الباكيند يقبله لطلبات GET فقط.
- عند وصول حدث realtime نعيد الجلب من الـ API ولا نعتمد على محتوى الحدث، لأن الحدث يحمل معرّفات فقط.
- في RTL: رسائل الزبون على اليمين، وردودنا على اليسار.
- الأرقام لاتينية، والتواريخ بتوقيت `Africa/Tripoli`.
- في الإنتاج: nginx يوجّه `/api` و `/connect` و `/meta` و `/webhooks` إلى FastAPI، والباقي إلى Next. ومسار `/api/v1/events` يحتاج `proxy_buffering off` و `proxy_read_timeout 1h`.

## المراحل التالية بعد 6b (من خارطة الطريق)

| المرحلة | المحتوى |
|---|---|
| 7 | Agent Studio: تعديل البرامج والأسعار وقاعدة المعرفة وشخصية البوت من اللوحة |
| 7 | الفوترة من جهة الوكالة: المحفظة، الاشتراك، شحن بالقسيمة، رفع إيصال تحويل |
| 8 | بوابة Plutu، قوالب واتساب (للرد بعد 24 ساعة والمتابعة)، التحليلات |
| لاحقاً | تطبيق الجوال، تيك توك |
