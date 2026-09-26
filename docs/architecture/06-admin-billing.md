# المرحلة 4.5: لوحة الإدارة الداخلية والربط المساعد + محرك المحفظة الليبي

> الحالة: مكتملة (كود) — 2026-09-26. العملة: الدينار الليبي فقط. بدون ضرائب. المستندات: سندات قبض.

## Migrations

| Revision | المحتوى |
|---|---|
| 0007 `admin_foundation` | `admin_users`، `admin_tokens` (hash فقط)، `admin_audit_log` (append-only)، صلاحيات الدور `app_admin`، حالة القناة `needs_reauth` + `last_checked_at` / `last_error` |
| 0008 `billing_wallet` | `plans`، `plan_prices`، `wallets`، `wallet_ledger`، `receipts` + `receipt_counters`، `subscriptions`، `subscription_periods`، `voucher_batches`، `vouchers`، `voucher_redeem_attempts`، `manual_payments`، ودوال المال، وتعديل `claim_due_conversations` لإسكات البوت عند الإيقاف |

## الأدوار

| الدور | الاستخدام | ملاحظات |
|---|---|---|
| `app_user` | API العام + Worker | يقرأ ماليته فقط (RLS). الاسترداد عبر `redeem_voucher()` فقط |
| `app_admin` | Admin API (منفذ محلي 8001) | BYPASSRLS. **لا** يعدّل `wallets` / `wallet_ledger` / `receipts` مباشرة |
| `app_owner` | Alembic + السكربتات | — |

الدور `app_admin` يُنشأ بـ init script (قواعد جديدة) أو `docker/postgres/manual/create_app_admin_role.sql` (قواعد موجودة) قبل `alembic upgrade`.

## دوال المال (مصدر الحقيقة الوحيد)

| الدالة | العمل | الضمانات |
|---|---|---|
| `wallet_apply(tenant, direction, amount, source, reference_id, ...)` | كل حركة مال | قفل صف المحفظة، لا رصيد سالب (BL001)، idempotent عبر `(source, reference_id)` (BL002 عند التعارض)، دقة الدرهم (BL003) |
| `issue_receipt(ledger_id, reference, admin)` | سند قبض لقيد شحن | ترقيم `RC-YYYY-NNNNNN` بدون فجوات، idempotent |
| `subscription_renew(tenant, price_id, actor, actor_id, period_id)` | تجديد من المحفظة | خصم + فترة + تفعيل في transaction واحدة، التجديد المبكر يُضاف بعد الفترة الحالية، `period_id` = Idempotency-Key |
| `subscription_grant(tenant, plan_id, days, admin)` | أيام مجانية | بدون خصم، مسجلة كفترة `admin_grant` |
| `subscription_try_autorenew(tenant)` | بعد أي شحن | يعيد تفعيل الاشتراك المنتهي/في المهلة/الموقوف إذا كفى الرصيد |
| `subscription_lifecycle_tick()` | Worker كل ساعة | تجديد تلقائي، أو مهلة (grace_days)، أو إيقاف |
| `redeem_voucher(code_hash, ip, actor, actor_id)` | استرداد قسيمة | ذري، idempotent لنفس الوكالة، 5 محاولات فاشلة / 15 دقيقة |

## Admin API (`app/admin`)

المصادقة: `Authorization: Bearer adm_...` (عبر `scripts/create_admin.py`)، وكل عملية تُكتب في `admin_audit_log` داخل نفس الـ transaction.

| المجموعة | Endpoints | الدور |
|---|---|---|
| المكاتب | `POST/GET /admin/tenants`، `GET/PATCH /admin/tenants/{id}`، `POST /admin/tenants/{id}/staff` | support |
| الربط المساعد | `POST /admin/tenants/{id}/channels/whatsapp`، `POST .../channels/facebook-page`، `GET .../channels`، `POST /admin/channels/{id}/check`، `POST /admin/channels/{id}/status` | support |
| الخطط | `GET/POST /admin/plans`، `PATCH /admin/plans/{code}`، `PUT /admin/plans/{code}/prices` | finance |
| المحفظة | `GET /admin/tenants/{id}/wallet`، `POST .../wallet/credit`*، `POST .../wallet/adjust`* | finance |
| الاشتراك | `GET/PATCH /admin/tenants/{id}/subscription`، `POST .../subscription/renew`*، `POST .../subscription/grant` | finance |
| التحويلات | `POST /admin/tenants/{id}/manual-payments`، `GET /admin/manual-payments`، `POST /admin/manual-payments/{id}/approve \| reject` | finance |
| القسائم | `POST /admin/voucher-batches` (CSV مرة واحدة)، `GET /admin/voucher-batches`، `POST .../{id}/void`، `POST /admin/tenants/{id}/vouchers/redeem` | finance |
| السندات | `GET /admin/tenants/{id}/receipts`، `GET /admin/receipts/{id}` | الكل |

\* تتطلب هيدر `Idempotency-Key: <uuid>`.

## قرارات

- **الربط المساعد يتحقق من Meta قبل الحفظ:** `GET` للرقم أو الصفحة بالتوكن، ثم `subscribed_apps`، ثم التشفير والحفظ. قناة مسجلة لوكالة أخرى تُرفض بـ 409.
- **رموز القسائم:** 16 رقماً مع Luhn، ويُخزَّن `HMAC-SHA256(VOUCHER_PEPPER, code)` فقط. لا يُغيَّر الـ pepper بعد الإصدار.
- **فصل الحالتين:** `tenants.status` حالة الحساب إدارياً (الحظر يوقف حتى استقبال الرسائل)، و`subscriptions.status` حالة الفوترة (الإيقاف يُسكت البوت وتبقى الرسائل محفوظة).
- **خطأ 190 عند الإرسال** يحوّل القناة إلى `needs_reauth` تلقائياً.
- **ماسنجر وإنستغرام:** الربط والاشتراك يعملان الآن، والأحداث تُحفظ خاماً، والرد الآلي بعد الـ parsers في المرحلة 5.

## التحقق (بيئة العمل)

- 14 اختبار مال بدور `app_admin` و5 بدور `app_user` على Postgres. من ضمنها: لا تعديل مباشر للأرصدة، idempotency، لا سحب على المكشوف، سندات تسلسلية، تجديد مبكر/متأخر، مهلة ثم إيقاف ثم إعادة تفعيل، إسكات البوت أثناء الإيقاف، القسائم وحد المحاولات، الموافقة المكررة، مجموع الدفتر = الرصيد.
- كل SQL الـ Admin API (51 استعلاماً) جُهّز كـ prepared statements بدور `app_admin`، وسيناريو تسجيل مكتب كامل نُفّذ فعلياً.
- 65 اختبار unit نجحت.
- لم يُشغَّل هنا: FastAPI وasyncpg (غير متاحة في بيئة العمل).
