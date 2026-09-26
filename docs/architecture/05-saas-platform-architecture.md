# معمارية المنصة التجارية ذاتية الخدمة (المراحل 5–8)

> الحالة: تصميم مقترح للنقاش — 2026-09-26
> النطاق: الربط الذاتي للقنوات (OAuth / Embedded Signup)، محرك الفوترة الليبي (Plutu + القسائم + التحويل)، وخارطة الطريق.
>
> **قرار 2026-09-26:** المنصة ليبية بالكامل — عملة واحدة (LYD)، بدون Stripe/Paddle وبدون كيانات خارجية، وبدون ضرائب. المستندات المالية = سندات قبض وتأكيدات اشتراك.

---

## 0. قيود حاكمة يجب حسمها قبل الكود

| القيد | الأثر | الإجراء |
|---|---|---|
| **Meta Tech Provider** | Embedded Signup لعملاء خارجيين يتطلب أن نكون Tech Provider (أو Solution Partner) | تقديم طلب Business Verification + App Review **الآن** بالتوازي مع التطوير |
| **صلاحيات App Review** | `whatsapp_business_management`, `whatsapp_business_messaging`, `pages_messaging`, `pages_manage_metadata`, `pages_show_list`, `business_management`, `instagram_basic`, `instagram_manage_messages` | فيديو screencast لكل صلاحية + سياسة خصوصية + Data Deletion Callback |
| **دفع رسوم Meta** | قوالب واتساب تُحاسَب على وسيلة الدفع في حساب واتساب الوكالة، وكثير من الأنشطة الليبية بلا بطاقات دولية | ردود خدمة العملاء داخل نافذة 24 ساعة لا تحتاج قوالب؛ إشعارات الموظفين تنتقل لـ Push (المرحلة 8) بدل قوالب واتساب |
| **رصيد الاتصالات كوسيلة دفع** | لا توجد API عامة من ليبيانا/المدار للتحقق من كروت الشحن؛ وقبول الرصيد قد يحتاج اتفاقاً مع المشغّل | قسائم خاصة بالمنصة (نموذج Libyan Spider) + تحويل الرصيد كمسار يدوي بعلاوة سعر |

**توصية تنفيذية:** أول 10–20 عميل بـ **Assisted Onboarding** (نحن نربط القنوات من لوحة الإدارة بالأدوات الحالية) حتى تصل موافقة Tech Provider. الـ self-serve يُبنى بالتوازي ويُفعَّل لاحقاً بـ feature flag.

---

## 1. الهوية والتسجيل الذاتي

### نموذج البيانات

```
users           (id, email, phone_e164, password_hash argon2id, email_verified_at, phone_verified_at, locale)
                -- عام، غير مرتبط بوكالة: شخص واحد قد يدير أكثر من نشاط
memberships     (user_id, tenant_id, role owner|admin|agent|sales, status invited|active)
invitations     (tenant_id, email|phone, role, token_hash, expires_at)
sessions        (user_id, refresh_token_hash, device, expires_at, revoked_at)  -- refresh tokens دوّارة
audit_log       (tenant_id, user_id, action, target, ip, created_at)           -- RLS
tenants         + business_type (travel_hajj_umrah | retail | clinic | services)
                + billing_country, billing_currency, onboarding_state jsonb
```

- `users` و`sessions` خارج RLS الوكالات (هوية عامة)، وكل ما عداها يحمل `tenant_id`.
- الـ API يقرأ `tenant_id` من JWT (claim `tid`) ويتحقق من العضوية، ثم يفتح `tenant_session(tid)`. نفس ضمانة العزل المطبقة في الـ worker.
- Access token قصير (15 دقيقة) + Refresh token دوّار في cookie من نوع httpOnly. تطبيق الجوال يخزنه في Secure Storage.
- التحقق من الهاتف: OTP عبر قالب واتساب من فئة Authentication من رقم المنصة، مع SMS احتياطي.

### مسار التسجيل

```
Signup (email/phone + password) -> تحقق OTP -> إنشاء المنظمة (الاسم، النشاط، الدولة)
   -> Trial تلقائي (14 يوم، حصص محدودة) -> Checklist:
      ① ربط قناة  ② الكتالوج  ③ ملفات المعرفة  ④ تجربة في Sandbox Chat  ⑤ Go Live
```

### القطاعات (Vertical Packs)

كل `business_type` = حزمة تشمل: جداول الكتالوج، أدوات الـ Agent، قالب البرومبت، وأعمدة تصدير الـ Leads.

- `travel_hajj_umrah`: الحالي (packages / departures / prices).
- `retail`: products / variants / stock / delivery_zones، والتصدير لشركات التوصيل.
- `clinic` / `services`: services / slots / appointments.

`ToolRegistry` يُبنى لكل وكالة حسب حزمتها، والنواة (الحلقة، RLS، القنوات، الفوترة) مشتركة.

---

## 2. الربط الذاتي لقنوات Meta

### 2.1 جداول

```
channel_accounts      (الحالي) + status: pending|active|needs_reauth|disconnected|error
                        + display_phone_number, verified_name, quality_rating, coexistence bool
channel_credentials   (tenant_id, channel_account_id, token_enc, token_type
                         business_system_user|page|ig_user, scopes[], expires_at,
                         last_validated_at, key_version)      -- فصل الأسرار عن البيانات الوصفية
onboarding_sessions   (id, tenant_id, user_id, channel, state_nonce_hash, step, status,
                         meta_ids jsonb, error, created_at, expires_at 15m)
```

`access_token_enc` الحالي ينتقل إلى `channel_credentials` بـ migration. التشفير: `MultiFernet` مع `key_version` لتدوير المفاتيح دون إعادة ربط العملاء.

### 2.2 واتساب — Embedded Signup (Graph API v25.0)

```
[Dashboard]                              [API]                                 [Meta]
 زر "ربط واتساب"
   │ POST /api/v1/onboarding/whatsapp/start ─► onboarding_session + state (nonce)
   │◄─ {session_id, config_id}
 FB.login({config_id, response_type:'code',
           override_default_response_type:true,
           extras:{featureType: '' | 'whatsapp_business_app_onboarding'}})
   │ ◄── message event: {waba_id, phone_number_id, business_id}
   │ ◄── code  (صالح ~30 ثانية فقط!)
   │ POST /api/v1/onboarding/whatsapp/complete {session_id, code, waba_id, phone_number_id}
   │                                       ① GET /oauth/access_token (client_id, secret, code) ─►
   │                                          business integration system user token (يُشفَّر فوراً)
   │                                       ② POST /{waba_id}/subscribed_apps                      ─►
   │                                       ③ POST /{phone_number_id}/register {pin}               ─►
   │                                          (للأرقام الجديدة؛ PIN عشوائي 6 أرقام يُخزَّن مشفّراً)
   │                                       ④ GET /{phone_number_id}?fields=display_phone_number,
   │                                          verified_name,quality_rating,code_verification_status
   │                                       ⑤ UPSERT channel_accounts + channel_credentials
   │◄─ {status: active | action_required}
 الخطوة ⑥ (العميل): إضافة وسيلة دفع في WhatsApp Manager — تُعرض كمهمة في الـ Checklist
```

**قواعد التنفيذ:**

- **الـ code يُرسل للباكيند فوراً** من داخل الـ callback، ويُستبدل بالتوكن في أول خطوة قبل أي شيء آخر (مهلته 30 ثانية).
- **آلة حالات قابلة للاستئناف:** بعد نجاح ① يُخزَّن التوكن، وأي فشل في ②–④ يُعاد من نفس الخطوة (زر "أعد المحاولة") دون إعادة تسجيل الدخول.
- **الأمان:** `state` مرتبط بـ (tenant, user) وصالح 15 دقيقة ويُستخدم مرة واحدة. فقط owner/admin يربط القنوات.
- **تعارض الملكية:** إذا كان `(whatsapp, phone_number_id)` مربوطاً بوكالة أخرى يُرفض الربط ويُسجَّل في audit، ولا يُنقل تلقائياً أبداً.
- **Coexistence** (رقم يعمل على تطبيق WhatsApp Business): `featureType: whatsapp_business_app_onboarding`، ونشترك في webhooks `smb_message_echoes` و`smb_app_state_sync` و`history`. الـ echoes تُحوَّل لـ `is_echo=True`، فتعمل آلية Takeover تلقائياً عند رد الموظف من هاتفه. حد الإنتاجية 20 رسالة/ثانية، وهو كافٍ.

### 2.3 ماسنجر وإنستغرام — Facebook Login for Business

```
FB.login({config_id: <messenger_ig_config>, response_type:'code'})
 -> API: code -> user token -> long-lived user token (fb_exchange_token)
 -> GET /me/accounts?fields=id,name,access_token,instagram_business_account{id,username}
 -> المستخدم يختار الصفحات/الحسابات في الواجهة
 -> لكل صفحة: POST /{page_id}/subscribed_apps
       subscribed_fields=messages,messaging_postbacks,message_echoes,message_reads
 -> channel_accounts: (messenger, page_id) و (instagram, ig_account_id) بتوكن الصفحة
```

- توكن الصفحة المشتق من long-lived user token لا ينتهي بالوقت، لكنه يسقط عند تغيير كلمة السر أو سحب الصلاحيات، وهذا ما يغطيه الفحص الدوري في 2.4.
- **حساب إنستغرام بدون صفحة فيسبوك:** مسار ثانٍ لاحقاً عبر Instagram API with Instagram Login. توكنه يعيش 60 يوماً ويُجدَّد عبر `refresh_access_token` بعد 24 ساعة من إصداره.
- الـ parsers تُضاف في `channels/registry.py`: `page` ثم `messenger`، و`instagram` ثم `instagram`.

### 2.4 دورة حياة التوكن ومراقبة القنوات

| الآلية | التفاصيل |
|---|---|
| فحص دوري (كل 6 ساعات) | `GET /debug_token` بتوكن التطبيق: `is_valid`، `expires_at`، `scopes`. أي نقص يحوّل الحالة إلى `needs_reauth` |
| تجديد تلقائي | توكنات Instagram Login عند اليوم 50. الأنواع الأخرى لا تُجدَّد بل يُعاد الربط |
| كشف فوري | خطأ 190 في `sender.py` (موجود كخطأ دائم) يحوّل القناة إلى `needs_reauth` + تنبيه |
| Webhooks | `account_update` و`phone_number_quality_update` و`message_template_status_update`، إضافة إلى Deauthorize و Data Deletion callbacks |
| تنبيه العميل | بانر في لوحة التحكم + Push + بريد: "انقطع ربط واتساب — أعد الربط" بزر يفتح نفس المسار |
| فك الربط | `DELETE /{waba_id}/subscribed_apps` أو `/{page_id}/subscribed_apps`، ثم `status=disconnected`. المحادثات تبقى للأرشيف |

---

## 3. محرك الفوترة الليبي (LYD فقط)

### 3.1 المبادئ

1. **نواة مستقلة عن وسيلة الدفع:** خطط، اشتراكات، سندات، ومحفظة. كل وسيلة (Plutu، قسائم، تحويل) تنتهي بقيد شحن في نفس المحفظة.
2. **مسبق الدفع (Prepaid) بالكامل:** القنوات المحلية لا تدعم خصماً متكرراً من بطاقة محفوظة، فيُشترى الاشتراك كفترات (1/3/6/12 شهر). المحفظة بالدينار تُشحن بأي وسيلة، والتجديد التلقائي من رصيدها.
3. **عملة واحدة: الدينار الليبي** بدقة الدرهم (`numeric(14,3)`)، بدون ضرائب.
4. **مصدر الحقيقة = callback موقّع من المزوّد** وليس صفحة العودة في المتصفح.
5. **Idempotency في كل خطوة:** معرّف فريد لكل محاولة دفع، والتفعيل في نفس transaction تأكيد الدفع.

### 3.2 نموذج البيانات

```
plans              (code, name, limits jsonb {conversations_month, seats, channels,
                    knowledge_mb, model_tier}, features jsonb, is_public)
plan_prices        (plan_id, period_months 1|3|6|12, price_lyd, is_active)
subscriptions      (tenant_id, plan_id, status trialing|active|past_due|grace|suspended|cancelled,
                    provider local|stripe, current_period_start/end, auto_renew_from_wallet,
                    cancel_at_period_end, external_id)
invoices           (tenant_id, number INV-2026-000123 [تسلسل بدون فجوات عبر جدول counters],
                    currency, subtotal, total, status draft|open|paid|void, period_start/end,
                    issued_at, paid_at, pdf_key)
invoice_lines      (invoice_id, description, qty, unit_amount, amount)
payment_attempts   (id, tenant_id, invoice_id, provider plutu|stripe|voucher|manual,
                    method sadad|edfali|tlync|bank_card|voucher|bank_transfer|libyana|almadar|card,
                    amount, currency, merchant_ref UNIQUE, provider_ref, status
                    initiated|otp_sent|redirected|pending_review|succeeded|failed|expired,
                    raw_request/raw_callback jsonb, created_at, confirmed_at)
wallets            (tenant_id, currency, balance numeric CHECK (balance >= 0))
wallet_ledger      (id, tenant_id, currency, direction credit|debit, amount, balance_after,
                    source payment|voucher|transfer|renewal|refund|adjustment, ref_id, actor)
                    -- append-only؛ الرصيد يتغير فقط مع قيد في نفس الـ transaction
voucher_batches    (id, distributor, denomination, currency, qty, created_by, created_at)
vouchers           (id, batch_id, code_hash UNIQUE, last4, amount, status
                    issued|distributed|redeemed|void, redeemed_tenant_id, redeemed_at, expires_at)
manual_transfers   (tenant_id, payment_attempt_id, channel bank|libyana|almadar, amount,
                    sender_phone, sender_ref, proof_key, status pending|approved|rejected,
                    matched_sms_id, reviewed_by, reviewed_at)
sms_inbox          (id, device_id, from, body, received_at, parsed jsonb, matched_attempt_id)
usage_counters     (tenant_id, period_start, metric, value)  -- UPSERT value = value + n
billing_events     (outbox: إيصال، تذكير تجديد، تعليق، إعادة تفعيل)
```

جداول الوكالات تحت RLS. أما `vouchers` و`voucher_batches` و`sms_inbox` فجداول منصة تُدار من لوحة الإدارة بدور منفصل.

### 3.3 Plutu (بوابة الدفع المحلية)

Plutu تجمع: سداد، أدفع لي، T-Lync (تداول، سداد، أدفع لي، موبي كاش، معاملات)، بطاقات المصارف المحلية (شبكة نمو)، وMPGS.

**أ) مسار إعادة التوجيه (T-Lync / بطاقات محلية)، ويُعتمد افتراضياً لأنه يغطي أكبر عدد وسائل بواجهة واحدة:**

```
POST /api/v1/billing/checkout {plan_price_id | wallet_topup_amount, method: tlync}
  -> invoice (open) + payment_attempt (merchant_ref = "PA-<ulid>")
  -> POST https://api.plutus.ly/api/v1/transaction/tlync/confirm
       Authorization: Bearer <access_token>, X-API-KEY: <api_key>
       {amount: "150.00", invoice_no: merchant_ref, mobile_number, return_url, callback_url, lang: ar}
  <- {code: CHECKOUT_REDIRECT, redirect_url}  -> المتصفح يُحوَّل
  ...الزبون يدفع...
  Plutu -> POST /webhooks/plutu  {gateway, approved, invoice_no, amount, transaction_id,
                                  paymet_method, hashed}
     1) تحقق التوقيع: HMAC-SHA256(secret, كل الحقول عدا hashed) بحروف كبيرة، مقارنة ثابتة الزمن
     2) approved == 1 و amount == المبلغ المتوقع و invoice_no موجود وغير منفّذ
     3) transaction واحدة: attempt=succeeded، invoice=paid، تمديد الاشتراك أو شحن المحفظة،
        قيد ledger، billing_event(receipt)
     4) رد 200 (إعادة الإرسال من Plutu آمنة بفضل الشرط في 2)
  Browser -> return_url?approved&invoice_no&hashed -> صفحة "جارٍ التأكيد" تستعلم حالة الـ attempt فقط
```

> ⚠ الحقل مكتوب في توثيق Plutu `paymet_method` (خطأ إملائي في المصدر). يُقرأ كما هو، وخوارزمية ترتيب الحقول في التوقيع تُطابَق حرفياً مع مثال Plutu الرسمي (PHP package) واختبار في Sandbox قبل الإنتاج.

**ب) مسار OTP المباشر (سداد / أدفع لي)، لتجربة داخل الصفحة بدون إعادة توجيه:**

```
verify  POST /transaction/edfali/verify  {mobile_number: 09XXXXXXXX, amount}  -> process_id (+ OTP للزبون)
confirm POST /transaction/edfali/confirm {process_id, code: OTP, amount, invoice_no: merchant_ref}
        -> transaction_id  => نفس خطوة التفعيل (3) أعلاه، بشكل متزامن
```

- `invoice_no` لا يُعاد استخدامه أبداً (شرط Plutu)، لذلك كل محاولة لها `merchant_ref` جديد حتى لنفس الفاتورة.
- محاولات عالقة (`redirected` لأكثر من 30 دقيقة): مهمة مصالحة (reconciliation) تستعلم Plutu إن توفّر endpoint للاستعلام، وإلا تُعرض لمراجعة يدوية مع كشف Plutu اليومي.
- مفاتيح Plutu (API key، access token، secret) في secret manager، مع فصل Sandbox عن Production.

### 3.4 القسائم الخاصة بالمنصة (بديل كروت الشحن)

بدل محاولة استرداد كروت ليبيانا/المدار (لا API عامة)، تُصدر المنصة **قسائم رقمية خاصة بها** تُباع عبر الموزعين ونقاط البيع وتطبيقات المصارف والمحافظ. هذا نفس النموذج المطبّق محلياً في Libyan Spider.

- **التوليد:** 16 رقماً عشوائياً آمناً (`secrets`) + رقم تحقق Luhn لكشف أخطاء الإدخال قبل الاستعلام. يُخزَّن فقط `sha256(pepper + code)` وآخر 4 أرقام. التصدير للموزع بملف CSV مشفّر مرة واحدة.
- **الاسترداد:** `POST /billing/vouchers/redeem {code}`:
  - Rate limit: 5 محاولات لكل 15 دقيقة لكل وكالة/IP، ثم قفل مؤقت + تنبيه.
  - `UPDATE vouchers SET status='redeemed' ... WHERE code_hash=$1 AND status='distributed' AND (expires_at IS NULL OR expires_at > now()) RETURNING amount`، وهي عملية ذرية تمنع الاستخدام المزدوج.
  - في نفس الـ transaction: قيد ledger (credit) ثم تجديد تلقائي إن كان مفعّلاً والرصيد كافياً.
- **الموزعون:** دفعة (batch) لكل موزع، وتقرير مبيعات واسترداد، وإلغاء دفعة كاملة عند التسريب.

### 3.5 تحويل الرصيد والتحويل المصرفي (مسار شبه يدوي)

```
الوكالة تختار "تحويل رصيد ليبيانا/المدار" أو "تحويل مصرفي"
 -> payment_attempt(status=pending_review) + تعليمات: الرقم/الحساب + المبلغ + مرجع قصير
 -> الوكالة ترفع لقطة الإيصال (proof) + رقم المرسل
 -> مطابقة مساعدة: هاتف Android على شريحة الشركة يعمل كـ SMS gateway، يرسل رسائل
    "استلمت رصيد" إلى /internal/sms (موقّعة) -> تحليل المبلغ والمرسل -> مطابقة واحدة فريدة
    (نفس المبلغ + نفس المرسل + خلال ساعتين) => موافقة تلقائية؛ غير ذلك => قائمة مراجعة
 -> الموافقة (آلية أو من المشرف) => نفس خطوة التفعيل
```

- صيغ رسائل المشغّلين تتغير، لذلك المحلّل مساعد وليس مصدر حقيقة، وأي غموض يذهب للمراجعة اليدوية.
- الرصيد المستلم يُخصم منه عند التسييل، فيُسعَّر هذا المسار بعلاوة (مثلاً +10–15%). ويُراجع قانونياً مع المشغّل قبل الإطلاق التجاري.

### 3.6 (ملغى) الدفع الدولي

مستبعد بقرار 2026-09-26: السوق المستهدف ليبي بالكامل.

### 3.7 الحصص والتنفيذ (Entitlements)

| المقياس | أين يُعدّ | عند التجاوز |
|---|---|---|
| المحادثات الشهرية (جلسة = أول رسالة زبون بعد 24 ساعة صمت) | `ingest.py` عند فتح/تجديد جلسة | +10% سماح ثم يتوقف البوت (الرسائل تُخزَّن دائماً) + تنبيه للمالك |
| رسائل الـ AI / التوكنز | `agent_runs` (موجود) | تقارير + حد أمان للتكلفة |
| مقاعد الموظفين | إنشاء الدعوات | منع دعوة جديدة |
| القنوات | الربط الذاتي | منع ربط قناة إضافية |
| حجم المعرفة | الرفع | رفض الملف مع رسالة واضحة |

`EntitlementService.check(tenant, metric)` يُستدعى في نقاط الإدخال، والنتيجة تُخزَّن مؤقتاً 60 ثانية.

**دورة حياة الاشتراك:**

```
trialing -> active -> (انتهاء الفترة بدون دفع) past_due -> grace (3 أيام: البوت يعمل + بانر)
         -> suspended (البوت متوقف، البيانات محفوظة، الدخول للوحة متاح للدفع) -> active عند الدفع
```

التذكير عند T-7 وT-3 وT-0 عبر Push/بريد/داخل اللوحة.

### 3.8 الفواتير

- سندات قبض وتأكيدات اشتراك PDF عبر WeasyPrint (HTML/CSS مع `dir=rtl` وخط Noto Naskh Arabic) بالدينار فقط، بترقيم تسلسلي بدون فجوات (`RC-2026-000001`).
- تُخزَّن في Object Storage (Hetzner / R2) وتُنزَّل برابط موقّع مؤقت.

---

## 4. الواجهات (ملخص قرارات المراحل 6 و8)

| الطبقة | القرار | السبب |
|---|---|---|
| Web Dashboard | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query + next-intl | RTL-first، مكونات جاهزة، SSR لصفحات التسويق |
| عقد الـ API | FastAPI `/api/v1` REST + OpenAPI ثم عميل TS مولَّد (openapi-typescript) | نوع واحد من الباكيند للواجهة والجوال |
| الزمن الحقيقي | WebSocket في FastAPI + Redis Pub/Sub (Redis يدخل هنا) | Inbox حي وتحديث الـ Leads؛ الـ worker ينشر الأحداث |
| Takeover | زر يضبط `conversations.mode='human'`، مع Echo detection (موجود جزئياً) | الموظف يرد من اللوحة أو التطبيق أو من هاتفه (Coexistence) |
| رفع المعرفة | PDF/TXT/DOCX إلى Object Storage، ثم مهمة استخراج (pypdf/docx) ثم التقطيع والـ embeddings (الكود موجود) مع حالة processing/ready/failed | معالجة خلفية مع تقدم ظاهر |
| الجوال | React Native (Expo) + Expo Notifications/FCM | مشاركة TypeScript والعميل المولَّد مع الويب. Flutter بديل إن كان الفريق أقوى في Dart |
| Push | جدول `devices` + outbox `notifications`، والأحداث: lead جديد، طلب تدخل، انقطاع قناة، قرب انتهاء الاشتراك | يحل محل قوالب واتساب لإشعار الموظفين (بدون تكلفة Meta) |

---

## 5. خارطة الطريق

| المرحلة | المحتوى | معيار الإنجاز | تقدير* |
|---|---|---|---|
| **4** ✅ | Agent + أدوات + RAG + إشعار + تقييم | evals ≥ 90% على الموديل المختار + تجربة حية مع الوكالة التجريبية | منجز (كود) |
| **4.5** ✅ | لوحة إدارة داخلية (Admin API): تسجيل المكاتب، الربط المساعد، المحفظة والاشتراكات، القسائم، مراجعة التحويلات | تشغيل 3–5 عملاء فعليين بربط مساعد | منجز (كود) — راجع 06 |
| **5** | الهوية والتسجيل + Embedded Signup + Facebook Login (Pages/IG) + parsers ماسنجر وإنستغرام + مراقبة التوكنات | عميل جديد يربط واتساب وإنستغرام بنفسه في أقل من 10 دقائق (بعد موافقة Tech Provider) | 4–5 أسابيع |
| **6** | Dashboard: Channels Hub، Agent Studio (كتالوج، معرفة، شخصية)، Live Inbox + Takeover، Leads CRM + تصدير، Analytics | الوكالة تدير يومها كاملاً من اللوحة | 6–8 أسابيع |
| **7** | الفوترة للوكالات: Plutu (T-Lync + OTP) بالدينار، استرداد القسائم من لوحة الوكالة، رفع إيصالات التحويل + مطابقة SMS، سندات PDF، الحصص | دفع محلي يفعّل الاشتراك خلال ثوانٍ؛ مصالحة يومية بدون فروق | 4–5 أسابيع |
| **8** | تطبيق الجوال: Push، Inbox سريع، Leads | الإشعار يصل خلال أقل من 5 ثوانٍ من إنشاء الـ lead | 4–6 أسابيع |

\* تقديرات لفريق صغير (2–3 مطورين). المراحل 6 و7 يمكن أن تتداخلا جزئياً.

**مسارات موازية تبدأ فوراً:** طلب Tech Provider + App Review، عقد Plutu (Sandbox)، والمراجعة القانونية لقبول تحويل الرصيد.

---

## المصادر

- Meta — [Embedded Signup: Implementation (v4, Graph API v25.0)](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/implementation)
- Meta — [Onboarding customers as a Tech Provider](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-customers-as-a-tech-provider)
- Meta — [Onboard WhatsApp Business app users (Coexistence)](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users)
- Meta — [Business Login for Instagram](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login)
- Plutu — [Payment Gateways](https://plutu.ly/en/payment-gateways/) · [T-Lync API](https://docs.plutu.ly/api-documentation/payments/t-lync) · [Adfali API](https://docs.plutu.ly/api-documentation/payments/adfali)
- Libyan Spider — [Voucher top-up model](https://help.libyanspider.com/kb-article/recharge-wallet-balance-using-libyan-spider-voucher/)
