# قالب «محل حلويات» (أول عميل: حلويات شهرزاد)

بوت واتساب يرد على زبائن المحل باللهجة الليبية:

- يفهم الرسائل النصية والصوتية.
- يعرض الأصناف بأسعار القائمة بالضبط (بالدينار).
- يأخذ الطلب (الأصناف، والكمية، والاستلام أو التوصيل، والموعد، والاسم).
- يرسل لصاحب المحل على واتساب تنبيهاً بكل طلب مكتمل، أو بكل زبون يحتاج رداً بشرياً.

القرارات وما تم اختباره: `docs/architecture/12-sweets-template.md`.

## كيف يعمل

```
Meta ──► /webhook/whatsapp ──► التحقق من التوقيع ──► حفظ الرسالة (المكرر يُتجاهل)
                                                       │
                                     صوت؟ ──► تنزيل ──► تفريغ (gpt-4o-transcribe)
                                                       ▼
        انتظار 4 ثوانٍ: هل كمّل الزبون كلامه؟ وهل انتهى تفريغ صوته؟
                                                       ▼
        القائمة + آخر 24 رسالة ──► OpenAI (JSON) ──► الرد للزبون
                                                       ▼
        طلب مكتمل جديد / يحتاج رد بشري ──► تنبيه صاحب المحل على واتساب
```

| الملف | المحتوى |
|---|---|
| `schema.sql` | جداول بيانات البوت في قاعدة العميل: الإعدادات، والقائمة، والزبائن، والرسائل، والطلبات، والأخطاء |
| `menu.example.csv` | شكل ملف القائمة. **الأسعار فيه أمثلة، وليست أسعار شهرزاد** |
| `bot.example.env` | الإعدادات والأسرار المطلوبة لكل عميل |
| `src/` | **المصدر:** منطق كل Code node، والـ prompt باللهجة الليبية، والـ JSON schema للرد |
| `build.py` | يبني `workflows/*.json` من `src/`. شغّله بعد أي تعديل: `python3 build.py` |
| `workflows/` | ما يُستورد في n8n: `[WA] رسائل واتساب`، و `[System] الأخطاء`، و `[System] تنظيف يومي` |
| `tests/` | اختبارات منطق الـ Code nodes بنفس الكود المبني |

## تشغيل بوت شهرزاد (على السيرفر)

البنية (Caddy + Postgres) شغالة من قبل، كما في `agency/README.md`.

### 1. من Meta (داخل حساب أعمال المحل نفسه)

1. **التطبيق والرقم:**
   - في developers.facebook.com أنشئ تطبيقاً من نوع Business، وأضف له WhatsApp.
   - أضف رقم المحل وفعّله، وخذ **Phone number ID** من صفحة API Setup.
2. **التوكن:** من Business Settings ← System users، أنشئ مستخدماً بصلاحية Admin.
   - أعطه أصول التطبيق وحساب واتساب.
   - ولّد توكناً دائماً بصلاحيتي `whatsapp_business_messaging` و `whatsapp_business_management`.
3. **App Secret:** من App Settings ← Basic.
4. **قالب تنبيه المالك:** من WhatsApp Manager ← Message templates، أنشئ قالباً:
   - الاسم `new_order`، والفئة **Utility**، واللغة العربية.
   - النص:
     ```
     تنبيه من بوت المحل: {{1}} ({{2}}) — {{3}}. التفاصيل: {{4}}. رد عليه من واتساب.
     ```
   - {{1}} الاسم، و{{2}} الهاتف، و{{3}} الأصناف أو سبب التحويل، و{{4}} الاستلام والموعد والمجموع.
   - Meta يطلب أمثلة للمتغيرات عند الإرسال. الاعتماد يأخذ من دقائق إلى يوم.
5. **الدفع:** أضف وسيلة دفع في WhatsApp Manager. رسائل القوالب (تنبيه المالك) مدفوعة لدى Meta، وردود البوت على الزبون داخل 24 ساعة مجانية.

### 2. OpenAI

- أنشئ مشروعاً باسم `shahrazad` في platform.openai.com، وضع له سقف إنفاق شهري.
- أنشئ مفتاحاً داخل هذا المشروع.

### 3. على السيرفر

```bash
cd /opt/agency
./scripts/new-client.sh shahrazad                       # إن لم يكن موجوداً
cp templates/sweets/bot.example.env clients/shahrazad.bot.env
cp templates/sweets/menu.example.csv clients/shahrazad.menu.csv
nano clients/shahrazad.bot.env                          # القيم من Meta و OpenAI ومعلومات المحل
nano clients/shahrazad.menu.csv                         # قائمة شهرزاد الحقيقية بأسعارها
./scripts/deploy-bot.sh shahrazad sweets
```

السكربت آمن لإعادة التشغيل. تعديل القائمة أو الإعدادات = عدّل الملف وأعد نفس الأمر.

### 4. ربط الـ webhook

1. في Meta ← WhatsApp ← Configuration ← Webhook:
   - **Callback URL:** `https://shahrazad.<BASE_DOMAIN>/webhook/whatsapp`
   - **Verify token:** قيمة `META_VERIFY_TOKEN`.
   - اضغط Verify and save، ثم اشترك في الحقل `messages`.
2. اشترك التطبيق في حساب واتساب (مرة واحدة):
   ```bash
   curl -X POST "https://graph.facebook.com/v23.0/<WABA_ID>/subscribed_apps" -H "Authorization: Bearer <WHATSAPP_TOKEN>"
   ```
3. حوّل التطبيق إلى Live: يطلب Meta رابط سياسة خصوصية.
4. أرسل رسالة لرقم المحل من هاتفك وتابع:
   ```bash
   docker compose logs -f --tail 50 n8n-shahrazad
   ```

## القائمة (`clients/<اسم>.menu.csv`)

```
category,name,unit,price_lyd,min_qty,description,available,sort_order
حلويات شرقية,بقلاوة بالفستق,كيلو,85,0.5,,true,10
```

- احفظه بترميز **CSV UTF-8** إذا عدّلته في Excel.
- `price_lyd` بالدينار الليبي. `min_qty` اختياري.
- لإخفاء صنف مؤقتاً: `available` = `false`.
- صنف تحذفه من الملف يُحذف من القائمة عند إعادة النشر.
- البوت لا يذكر إلا الأصناف المتاحة، وبأسعارها حرفياً.

## كيف يعرف الزبون الأسعار

1. **يسأل عن صنف** («قداش كيلو البقلاوة؟»):
   - الوكيل يرد بالسعر الحرفي من القائمة.
   - القائمة كاملة موجودة في كل طلب لـ OpenAI، من `menu.csv` عبر قاعدة العميل.
2. **يطلب المنيو كله** («المنيو»، «نبي نشوف الأسعار»، «شن عندكم؟»، «menu»):
   - يصله الرد فوراً من القاعدة، **بدون أي استدعاء للذكاء الاصطناعي**.
   - إذا كان `MENU_IMAGE_URL` موجوداً: صورة منيو المحل بتعليق يشرح طريقة الطلب.
   - إذا لم يكن: قائمة الأسعار نصاً بالفئات والوحدات. القائمة الطويلة تُقسم على أكثر من رسالة تحت حد واتساب.
   - سؤال عن صنف محدد («أسعار البقلاوة») يروح للوكيل، ليرد عليه بالضبط.
3. **صورة المنيو** رابط https عام (مثلاً من استضافة صور).
   - الوكيل يعتمد أسعار `menu.csv`، فحدّث الصورة والملف معاً.

## توفير توكنز الذكاء الاصطناعي

قياس بنفس الكود المبني (مُرمِّز o200k الذي يستخدمه gpt-4o)، لرد عادي بعد 10 رسائل:

| | قائمة 11 صنفاً | قائمة 60 صنفاً |
|---|---|---|
| مدخل لكل رد | ~1,700 توكن | ~3,050 توكن |
| منها بداية ثابتة لكل الزبائن (تعليمات + قائمة + schema) | ~1,470 (86%) | ~2,820 (92%) |
| مخرج لكل رد | 100–250 | 100–250 |

**ما يقلل الاستهلاك (مطبّق):**

| الإجراء | الأثر |
|---|---|
| **بداية ثابتة:** التعليمات والقائمة في رسالة أولى لا تتغير، والوقت والزبون والطلب الجاري في رسالة ثانية قصيرة (~50 توكن) | OpenAI يخزّن البداية المتكررة تلقائياً (من 1024 توكن فما فوق) ويخصم سعرها. قبل هذا التعديل كان رقم الزبون في وسط التعليمات، فالبداية المتطابقة ~550 توكن فقط، تحت الحد، فلا خصم |
| **المنيو بدون ذكاء اصطناعي** | طلب «المنيو/الأسعار» = صفر توكنز |
| **تجاهل التفاعلات** (👍 على رسالة البوت) وإشعارات النظام | كانت تستدعي الوكيل بلا داعٍ |
| **محادثة أقصر:** آخر `history_messages` (12) رسالة خلال `history_hours` (48 ساعة) فقط | محادثة الأسبوع الماضي لا تُرسل |
| **«الطلب الجاري»:** يُحفظ مع الزبون ويُعطى للوكيل في سطر واحد | تقصير المحادثة لا يُضيّع أصناف الطلب |
| **الانتظار 4 ثوانٍ** قبل الرد | 3 رسائل متتالية = استدعاء واحد |
| **حد الردود لكل زبون** `max_replies_per_hour` (30) | من يرسل بلا توقف لا يستنزف الرصيد |
| **سقف الإنفاق** في مشروع OpenAI الخاص بالعميل | حماية أخيرة من أي خطأ |

**أكبر توفير إضافي: الموديل.**

- غيّر `CHAT_MODEL=gpt-4o-mini` و `TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe` في `clients/<اسم>.bot.env`، ثم أعد النشر.
- سعر التوكن في نسخ mini أقل بكثير من gpt-4o (راجع صفحة أسعار OpenAI الحالية).
- جرّبه في الأسبوع التجريبي، وقارن جودة اللهجة والالتزام بالأسعار قبل اعتماده.

**قياس الاستهلاك الفعلي** (كل رد محفوظ بتوكنزه):

```sql
SELECT date_trunc('month', created_at) AS month, ai_model, count(*) AS replies,
       sum(prompt_tokens) AS input, sum(cached_tokens) AS cached_input, sum(completion_tokens) AS output
FROM messages WHERE direction = 'out' AND ai_model IS NOT NULL
GROUP BY 1, 2 ORDER BY 1 DESC;
```

- التكلفة = المدخل غير المخزّن × سعر المدخل + المخزّن × سعر المدخل المخزّن + المخرج × سعر المخرج.
- الأسعار من صفحة OpenAI.
- `cached_input` قريب من 0 بعد أسبوع تشغيل؟ معناه أن البداية تتغير بين الطلبات؛ راجع `src/build_request.js`.

**إعدادات متقدمة** (في قاعدة العميل، بدون إعادة نشر):

```sql
UPDATE bot_settings SET history_messages = 10, history_hours = 24, max_replies_per_hour = 20, debounce_seconds = 6;
```

## متابعة العميل (بدون لوحة تحكم)

```bash
docker compose exec postgres psql -U shahrazad -d shahrazad
```

```sql
-- آخر الطلبات
SELECT id, created_at, customer_name, phone, items, fulfillment, address, needed_at, estimated_total_lyd, notified_at
FROM orders ORDER BY id DESC LIMIT 20;
-- محادثة زبون
SELECT m.created_at, m.direction, m.type, m.text FROM messages m JOIN contacts c ON c.id = m.contact_id
WHERE c.wa_id = '2189xxxxxxxx' ORDER BY m.id;
-- الأخطاء (فشل إرسال، تنبيه مالك مرفوض...)
SELECT * FROM workflow_errors ORDER BY id DESC LIMIT 20;
```

- **التنظيف يدوياً** (يحدث تلقائياً كل ليلة الساعة 3):
  ```bash
  docker compose exec -e N8N_RUNNERS_BROKER_PORT=5690 n8n-shahrazad n8n execute --id=botCleanupFlow01
  ```

## الاختبار

```bash
node --test agency/templates/sweets/tests/code_nodes.test.js   # منطق الـ Code nodes: ثانية واحدة
agency/dev/e2e.sh                                              # n8n حقيقي + بديل Meta/OpenAI: نحو 6 دقائق
```

- `e2e.sh` لا يحتاج حساب Meta ولا مفتاح OpenAI. يعمل في نسخة مؤقتة على المنفذين 18080 و18443، ويحذفها في النهاية.
- ردود البديل المحلي نص ثابت مُعلَّم «رد تجريبي من البديل المحلي». البديل لا يفهم الرسائل، فلا تحكم منه على جودة الردود. هو يختبر المسار كاملاً فقط: ما يُرسل لـ OpenAI، وما يصل للزبون والمالك، وما يُحفظ.

## تعديل البوت

1. عدّل في `src/`:
   - `system_prompt.txt` للأسلوب والقواعد.
   - `*.js` للمنطق.
   - `build.py` لترتيب العقد والاستعلامات.
2. ابنِ واختبر:
   ```bash
   python3 build.py && node --test tests/code_nodes.test.js && ../../dev/e2e.sh
   ```
3. انشر:
   ```bash
   ./scripts/deploy-bot.sh <عميل> sweets
   ```

لا تعدّل `workflows/*.json` يدوياً. إذا جرّبت تعديلاً في محرر n8n، انقله إلى `src/` ثم أعد البناء، وإلا ضاع عند النشر التالي.
