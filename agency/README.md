# وكالة الأتمتة: n8n + Caddy على VPS

كل عميل = نسخة n8n مستقلة + قاعدة بيانات خاصة + مفتاح تشفير خاص + نطاق فرعي HTTPS.
لماذا هذا الشكل، وكيف تُنظَّم الـ workflows داخل كل عميل: `docs/architecture/11-agency-n8n-multi-client.md`.

```
agency/
  docker-compose.yml     Caddy + Postgres + خدمة n8n لكل عميل (تُضاف تلقائياً في آخر الملف)
  caddy/Caddyfile        نطاق لكل عميل: الـ webhooks مفتوحة، والمحرر خلف كلمة مرور
  .env                   إعدادات الوكالة (من .env.example)          ← لا يُرفع لـ git
  clients/<اسم>.env      أسرار كل عميل ومفتاح تشفيره (يولّدها السكربت) ← لا يُرفع لـ git
  data/                  قواعد البيانات، ملفات n8n، شهادات HTTPS        ← لا يُرفع لـ git
  scripts/new-client.sh  إضافة عميل بأمر واحد
  scripts/deploy-bot.sh  نشر بوت من قالب لعميل (الجداول، القائمة، الـ credentials، الـ workflows)
  scripts/backup.sh      نسخة احتياطية لكل العملاء
  templates/sweets/      قالب «محل حلويات»: بوت واتساب نص وصوت + تنبيه صاحب المحل (أول عميل: شهرزاد)
  dev/                   للتجربة فقط: بديل محلي لـ Meta و OpenAI، وتجربة شاملة (e2e.sh)
```

## التشغيل خطوة بخطوة

### 0. قبل السيرفر: الـ DNS

في لوحة النطاق أضف سجلاً واحداً يكفي لكل العملاء:

```
A    *.bots.example.ly    →    <IP السيرفر>
```

كل عميل يأخذ نطاقاً فرعياً تلقائياً: `hajj.bots.example.ly`، `shop.bots.example.ly`...

### 1. تجهيز السيرفر (Ubuntu، مرة واحدة)

```bash
ssh root@<IP>
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh            # Docker + docker compose
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp && ufw --force enable
```

المنافذ المفتوحة 22 و80 و443 فقط. Postgres و n8n بدون أي منفذ خارجي، ولا يُوصل لهما إلا عبر Caddy.

### 2. نسخ الملفات

من جهازك (مجلد المستودع):

```bash
scp -r agency root@<IP>:/opt/agency
```

### 3. الإعدادات

على السيرفر:

```bash
cd /opt/agency
cp .env.example .env && chmod 600 .env
openssl rand -hex 24                                   # انسخ الناتج إلى POSTGRES_PASSWORD
docker run --rm -it caddy:2.11 caddy hash-password     # كلمة مرور المحرر => انسخ الناتج إلى EDITOR_HASH بين ' '
nano .env                                              # BASE_DOMAIN و ACME_EMAIL و POSTGRES_PASSWORD و EDITOR_HASH
```

### 4. التشغيل

```bash
docker compose up -d
docker compose ps                                      # caddy و postgres: running / healthy
```

### 5. أول عميل

```bash
./scripts/new-client.sh hajj
```

السكربت يفعل كل شيء:
1. يولّد الأسرار في `clients/hajj.env`.
2. ينشئ قاعدة البيانات `hajj` ومستخدماً لا يدخل غيرها.
3. يضيف الخدمة `n8n-hajj` والنطاق `hajj.bots.example.ly`.
4. يشغّلها.

ثم:

1. افتح `https://hajj.bots.example.ly`. أول زيارة تأخذ ثوانٍ لإصدار شهادة HTTPS.
2. أدخل `EDITOR_USER` وكلمة المرور، ثم أنشئ حساب n8n لهذا العميل (استخدم كلمة مرور مختلفة لكل عميل).
3. **احفظ نسخة من `clients/hajj.env` خارج السيرفر** (مدير كلمات مرور مثلاً). فيه مفتاح تشفير كل credentials العميل.

رابط الـ webhook الذي تضعه في Meta لهذا العميل يبدأ بـ `https://hajj.bots.example.ly/webhook/...`.

### 6. بوت العميل من قالب

```bash
cp templates/sweets/bot.example.env clients/hajj.bot.env     # عبّئه: Meta و OpenAI ومعلومات المحل
cp templates/sweets/menu.example.csv clients/hajj.menu.csv   # القائمة والأسعار بالدينار
./scripts/deploy-bot.sh hajj sweets
```

خطوات Meta وقالب تنبيه المالك ومتابعة الطلبات: `templates/sweets/README.md`.

### 7. النسخ الاحتياطي (مرة واحدة)

```bash
crontab -e
# أضف السطر:
30 3 * * *  /opt/agency/scripts/backup.sh >> /var/log/agency-backup.log 2>&1
```

- كل ليلة: `pg_dump` لقاعدة كل عميل، وملفاته، وأسراره في `backups/<التاريخ>/`. يُحفظ آخر 14 يوماً.
- **انسخ `backups/` خارج السيرفر** (Hetzner Storage Box أو أي تخزين). نسخة على نفس السيرفر لا تحمي من فقدانه.

## التشغيل اليومي

| المهمة | الأمر (من `/opt/agency`) |
|---|---|
| حالة كل شيء | `docker compose ps` |
| سجلات عميل | `docker compose logs -f --tail 100 n8n-hajj` |
| إعادة تشغيل عميل | `docker compose restart n8n-hajj` |
| استهلاك الذاكرة | `docker stats --no-stream` |
| عميل جديد | `./scripts/new-client.sh <اسم>` |
| نشر أو تحديث بوت (بعد تعديل القائمة أو الإعدادات) | `./scripts/deploy-bot.sh <اسم> <قالب>` |
| تجربة شاملة بدون Meta و OpenAI (على جهازك) | `dev/e2e.sh` |

### ترقية n8n

1. خذ نسخة احتياطية: `./scripts/backup.sh`.
2. اقرأ ملاحظات الإصدار الجديد.
3. غيّر `image: n8nio/n8n:<الإصدار>` في أعلى `docker-compose.yml`. السطر يطبّق على كل العملاء.
4. `docker compose pull && docker compose up -d`.

للتجربة على عميل واحد أولاً: أضف سطر `image:` بالإصدار الجديد تحت خدمته فقط، ثم احذفه بعد التعميم.

### إيقاف عميل وحذف بياناته

```bash
docker compose stop n8n-hajj && docker compose rm -f n8n-hajj
docker compose exec postgres psql -U postgres -c 'DROP DATABASE hajj' -c 'DROP ROLE hajj'
rm -rf data/n8n/hajj clients/hajj.env
# احذف كتلة n8n-hajj من docker-compose.yml وكتلة hajj.bots... من caddy/Caddyfile، ثم:
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

النسخ الاحتياطية القديمة للعميل تبقى في `backups/` حتى تنتهي مدتها (14 يوماً)، أو احذفها يدوياً إن طلب العميل حذف بياناته.

## حجم السيرفر

قياس فعلي لـ n8n 2.40.7 (بدون حمل):

| المكوّن | الذاكرة |
|---|---|
| n8n لكل عميل | ~330MB مستقرة، وحتى ~520MB بعد الإقلاع مباشرة |
| Postgres (مشترك) | 60–130MB |
| Caddy | 15–35MB |

- سيرفر 4GB: حتى ~6 عملاء بارتياح.
- سيرفر 8GB: حتى ~14 عميلاً.
- كل نسخة n8n محدودة بـ `N8N_MEM_LIMIT` (1GB)، فعميل معطوب لا يؤثر على الآخرين.
- **النقل لسيرفر ثانٍ:** عند الحاجة يُنقل عميل كامل (نسخته الاحتياطية + ملف `.env` + تغيير DNS لنطاقه)، لأن كل عميل مستقل.
