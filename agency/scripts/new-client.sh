#!/usr/bin/env bash
# إضافة عميل جديد (خلية معزولة):
#   قاعدة بيانات + مستخدم Postgres خاصان به، مفتاح تشفير خاص، حاوية n8n خاصة، نطاق فرعي HTTPS.
# الاستخدام (من مجلد agency):  ./scripts/new-client.sh hajj
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=${1:-}
if [[ ! "$NAME" =~ ^[a-z][a-z0-9]{1,19}$ ]]; then
  echo "الاسم: حروف إنجليزية صغيرة وأرقام (2 إلى 20)، يبدأ بحرف. مثال: hajj" >&2; exit 1
fi
case "$NAME" in postgres|template0|template1|caddy|n8n|public) echo "اسم محجوز: $NAME" >&2; exit 1 ;; esac
[ -f .env ] || { echo "أنشئ .env أولاً: cp .env.example .env" >&2; exit 1; }
set -a; . ./.env; set +a
: "${BASE_DOMAIN:?BASE_DOMAIN فارغ في .env}"

SERVICE="n8n-$NAME"
DOMAIN="$NAME.$BASE_DOMAIN"
ENV_FILE="clients/$NAME.env"
if grep -q "^  $SERVICE:" docker-compose.yml || [ -e "$ENV_FILE" ]; then
  echo "العميل $NAME موجود مسبقاً" >&2; exit 1
fi

echo "== 1) الأسرار: clients/$NAME.env"
mkdir -p clients "data/n8n/$NAME"
DB_PASSWORD=$(openssl rand -hex 24)
(umask 077; cat > "$ENV_FILE" <<EOF
# العميل: $NAME — لا ترفع هذا الملف لأي مكان.
# N8N_ENCRYPTION_KEY يفك تشفير كل credentials العميل: ضياعه = إعادة إدخالها كلها. احفظه في نسختك الاحتياطية.
N8N_HOST=$DOMAIN
N8N_EDITOR_BASE_URL=https://$DOMAIN/
N8N_WEBHOOK_URL=https://$DOMAIN/
N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)
DB_POSTGRESDB_DATABASE=$NAME
DB_POSTGRESDB_USER=$NAME
DB_POSTGRESDB_PASSWORD=$DB_PASSWORD
EOF
)
# n8n يعمل داخل الحاوية بالمستخدم 1000
if [ "$(id -u)" -eq 0 ]; then chown 1000:1000 "data/n8n/$NAME"; else sudo chown 1000:1000 "data/n8n/$NAME"; fi

echo "== 2) قاعدة البيانات: $NAME (لا يدخلها إلا المستخدم $NAME)"
docker compose up -d --wait postgres >/dev/null
docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U postgres <<SQL
CREATE ROLE "$NAME" LOGIN PASSWORD '$DB_PASSWORD';
CREATE DATABASE "$NAME" OWNER "$NAME";
REVOKE ALL ON DATABASE "$NAME" FROM PUBLIC;
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
\connect $NAME
CREATE SCHEMA n8n AUTHORIZATION "$NAME";
CREATE EXTENSION IF NOT EXISTS vector;
SQL

echo "== 3) خدمة $SERVICE في docker-compose.yml + $DOMAIN في caddy/Caddyfile"
cat >> docker-compose.yml <<EOF

  $SERVICE:
    <<: *n8n
    env_file: ./clients/$NAME.env
    volumes:
      - ./data/n8n/$NAME:/home/node/.n8n
EOF
printf '\n%s {\n\timport n8n_client %s\n}\n' "$DOMAIN" "$SERVICE" >> caddy/Caddyfile

echo "== 4) التشغيل"
if [ -n "$(docker compose ps -q caddy)" ]; then
  docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
else
  docker compose up -d caddy
fi
if ! docker compose up -d --wait "$SERVICE"; then
  echo "لم تصبح $SERVICE جاهزة. السبب في:  docker compose logs --tail 50 $SERVICE" >&2; exit 1
fi

cat <<EOF

✔ العميل $NAME جاهز
  المحرر:     https://$DOMAIN/          (دخول Caddy: $EDITOR_USER، ثم أنشئ حساب n8n لهذا العميل)
  Webhooks:   https://$DOMAIN/webhook/...
  احفظ نسخة من clients/$NAME.env في مكان آمن خارج السيرفر (فيه مفتاح التشفير).
EOF
