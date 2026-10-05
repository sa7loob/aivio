#!/usr/bin/env bash
# نشر بوت لعميل من قالب: الجداول، والإعدادات، والقائمة، والـ credentials، والـ workflows.
# آمن لإعادة التشغيل: إعادة النشر تحدّث القائمة والإعدادات والـ workflows ولا تكرر شيئاً.
#
# الاستخدام (من مجلد agency):  ./scripts/deploy-bot.sh shahrazad sweets
# يحتاج:
#   clients/<اسم>.env        من new-client.sh
#   clients/<اسم>.bot.env    من templates/<قالب>/bot.example.env (الأسرار ومعلومات المحل)
#   clients/<اسم>.menu.csv   من templates/<قالب>/menu.example.csv (القائمة والأسعار بالدينار)
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=${1:-}
TEMPLATE=${2:-}
if [[ ! "$NAME" =~ ^[a-z][a-z0-9]{1,19}$ ]] || [ -z "$TEMPLATE" ]; then
  echo "الاستخدام: ./scripts/deploy-bot.sh <اسم العميل> <القالب>   مثال: ./scripts/deploy-bot.sh shahrazad sweets" >&2
  exit 1
fi
TPL="templates/$TEMPLATE"
CLIENT_ENV="clients/$NAME.env"
BOT_ENV="clients/$NAME.bot.env"
MENU="clients/$NAME.menu.csv"
SERVICE="n8n-$NAME"
[ -d "$TPL/workflows" ] || { echo "قالب غير موجود: $TPL" >&2; exit 1; }
[ -f "$CLIENT_ENV" ] || { echo "العميل غير موجود. ابدأ بـ: ./scripts/new-client.sh $NAME" >&2; exit 1; }
[ -f "$BOT_ENV" ] || { echo "أنشئ $BOT_ENV من $TPL/bot.example.env" >&2; exit 1; }
[ -f "$MENU" ] || { echo "أنشئ $MENU من $TPL/menu.example.csv" >&2; exit 1; }

set -a; . "$BOT_ENV"; set +a
missing=()
for v in BUSINESS_NAME PHONE_NUMBER_ID META_APP_SECRET META_VERIFY_TOKEN WHATSAPP_TOKEN OPENAI_API_KEY OWNER_PHONE; do
  [ -n "${!v:-}" ] || missing+=("$v")
done
[ ${#missing[@]} -eq 0 ] || { echo "قيم ناقصة في $BOT_ENV: ${missing[*]}" >&2; exit 1; }
OWNER_PHONE=$(printf '%s' "$OWNER_PHONE" | tr -cd '0-9' | sed 's/^00//')
DB_PASSWORD=$(grep '^DB_POSTGRESDB_PASSWORD=' "$CLIENT_ENV" | cut -d= -f2-)
DOMAIN=$(grep '^N8N_HOST=' "$CLIENT_ENV" | cut -d= -f2-)
export DB_PASSWORD NAME

db() { docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U "$NAME" -d "$NAME" "$@"; }
# قيمة لـ \set في psql: بين ' ' مع مضاعفة ' و \ (القيم تمر عبر stdin، فلا تظهر الأسرار في قائمة العمليات)
q() { local v=${1//\\/\\\\}; v=${v//\'/\'\'}; printf "'%s'" "$v"; }

echo "== 1) الجداول (قاعدة $NAME)"
db < "$TPL/schema.sql"

echo "== 2) الإعدادات"
db <<SQL
\set business_name $(q "$BUSINESS_NAME")
\set phone_number_id $(q "$PHONE_NUMBER_ID")
\set app_secret $(q "$META_APP_SECRET")
\set verify_token $(q "$META_VERIFY_TOKEN")
\set owner_phone $(q "$OWNER_PHONE")
\set owner_template $(q "${OWNER_TEMPLATE_NAME:-}")
\set owner_template_lang $(q "${OWNER_TEMPLATE_LANG:-ar}")
\set opening_hours $(q "${OPENING_HOURS:-}")
\set address $(q "${ADDRESS:-}")
\set delivery_info $(q "${DELIVERY_INFO:-}")
\set order_notice $(q "${ORDER_NOTICE:-}")
\set extra $(q "${EXTRA_INSTRUCTIONS:-}")
\set chat_model $(q "${CHAT_MODEL:-gpt-4o}")
\set transcription_model $(q "${TRANSCRIPTION_MODEL:-gpt-4o-transcribe}")
\set graph_base $(q "${GRAPH_BASE_URL:-}")
\set openai_base $(q "${OPENAI_BASE_URL:-}")
\set menu_image $(q "${MENU_IMAGE_URL:-}")
\set max_replies $(q "${MAX_REPLIES_PER_HOUR:-30}")
INSERT INTO bot_settings AS s (id, business_name, phone_number_id, meta_app_secret, meta_verify_token, owner_phone,
    owner_template_name, owner_template_lang, opening_hours, address, delivery_info, order_notice,
    extra_instructions, chat_model, transcription_model, graph_base_url, openai_base_url,
    menu_image_url, max_replies_per_hour)
VALUES (1, :'business_name', :'phone_number_id', :'app_secret', :'verify_token', :'owner_phone',
    NULLIF(:'owner_template', ''), :'owner_template_lang', NULLIF(:'opening_hours', ''), NULLIF(:'address', ''),
    NULLIF(:'delivery_info', ''), NULLIF(:'order_notice', ''), NULLIF(:'extra', ''), :'chat_model',
    :'transcription_model',
    COALESCE(NULLIF(:'graph_base', ''), 'https://graph.facebook.com/v23.0'),
    COALESCE(NULLIF(:'openai_base', ''), 'https://api.openai.com/v1'),
    NULLIF(:'menu_image', ''), CAST(:'max_replies' AS int))
ON CONFLICT (id) DO UPDATE SET
    business_name = EXCLUDED.business_name, phone_number_id = EXCLUDED.phone_number_id,
    meta_app_secret = EXCLUDED.meta_app_secret, meta_verify_token = EXCLUDED.meta_verify_token,
    owner_phone = EXCLUDED.owner_phone, owner_template_name = EXCLUDED.owner_template_name,
    owner_template_lang = EXCLUDED.owner_template_lang, opening_hours = EXCLUDED.opening_hours,
    address = EXCLUDED.address, delivery_info = EXCLUDED.delivery_info, order_notice = EXCLUDED.order_notice,
    extra_instructions = EXCLUDED.extra_instructions, chat_model = EXCLUDED.chat_model,
    transcription_model = EXCLUDED.transcription_model, graph_base_url = EXCLUDED.graph_base_url,
    openai_base_url = EXCLUDED.openai_base_url, menu_image_url = EXCLUDED.menu_image_url,
    max_replies_per_hour = EXCLUDED.max_replies_per_hour, updated_at = now();
SQL

echo "== 3) القائمة: $MENU"
db -c "CREATE TEMP TABLE menu_in (category text, name text, unit text, price_lyd numeric, min_qty numeric,
                                  description text, available boolean, sort_order int)" \
   -c "\\copy menu_in FROM pstdin WITH (FORMAT csv, HEADER true)" \
   -c "BEGIN;
       DELETE FROM products WHERE name NOT IN (SELECT trim(name) FROM menu_in WHERE name IS NOT NULL);
       INSERT INTO products (category, name, unit, price_lyd, min_qty, description, available, sort_order)
       SELECT trim(category), trim(name), trim(unit), price_lyd, min_qty, NULLIF(trim(description), ''),
              COALESCE(available, true), COALESCE(sort_order, 0)
       FROM menu_in
       ON CONFLICT (name) DO UPDATE SET category = EXCLUDED.category, unit = EXCLUDED.unit,
           price_lyd = EXCLUDED.price_lyd, min_qty = EXCLUDED.min_qty, description = EXCLUDED.description,
           available = EXCLUDED.available, sort_order = EXCLUDED.sort_order;
       COMMIT;" < "$MENU"
db -At -c "SELECT count(*) || ' صنف، منها ' || count(*) FILTER (WHERE available) || ' متاح' FROM products"

echo "== 4) الـ credentials في n8n (مشفّرة بمفتاح العميل)"
# تُبنى في الذاكرة وتُمرَّر عبر stdin إلى ملف مؤقت داخل الحاوية يُحذف فوراً بعد الاستيراد
WHATSAPP_TOKEN="$WHATSAPP_TOKEN" OPENAI_API_KEY="$OPENAI_API_KEY" python3 -c '
import json, os
print(json.dumps([
    {"id": "botPostgres00001", "name": "DB", "type": "postgres",
     "data": {"host": "postgres", "port": 5432, "database": os.environ["NAME"], "user": os.environ["NAME"],
              "password": os.environ["DB_PASSWORD"], "ssl": "disable", "allowUnauthorizedCerts": False,
              "maxConnections": 5}},
    {"id": "botWhatsApp00001", "name": "WhatsApp", "type": "httpHeaderAuth",
     "data": {"name": "Authorization", "value": "Bearer " + os.environ["WHATSAPP_TOKEN"]}},
    {"id": "botOpenAI0000001", "name": "OpenAI", "type": "httpHeaderAuth",
     "data": {"name": "Authorization", "value": "Bearer " + os.environ["OPENAI_API_KEY"]}},
]))' | docker compose exec -T "$SERVICE" sh -c 'umask 077; cat > /tmp/bot-credentials.json'
docker compose exec -T "$SERVICE" sh -c \
  'n8n import:credentials --input=/tmp/bot-credentials.json; status=$?; rm -f /tmp/bot-credentials.json; exit $status' \
  2>&1 | grep -vE "^\s*$|deprecat|Permissions 0644" || true

echo "== 5) الـ workflows من $TPL"
published=()
for wf in "$TPL"/workflows/*.json; do
  id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$wf")
  docker compose exec -T "$SERVICE" sh -c 'cat > /tmp/wf.json' < "$wf"
  docker compose exec -T "$SERVICE" sh -c \
    'n8n import:workflow --input=/tmp/wf.json; status=$?; rm -f /tmp/wf.json; exit $status' 2>&1 \
    | grep -vE "^\s*$|deprecat" || true
  # كلها تُنشر، حتى workflow الأخطاء: n8n 2.x لا يشغّل Error Workflow غير منشور
  docker compose exec -T "$SERVICE" n8n publish:workflow --id="$id" 2>&1 | grep -iE "error|publish" | grep -v "^Note" || true
  published+=("$id")
done

echo "== 6) إعادة تشغيل $SERVICE لتفعيل الـ workflows"
docker compose restart "$SERVICE" >/dev/null 2>&1
docker compose up -d --wait "$SERVICE" >/dev/null 2>&1

cat <<EOF

✔ بوت $NAME منشور (قالب $TEMPLATE): ${published[*]}
  في Meta (WhatsApp > Configuration > Webhook):
    Callback URL:  https://$DOMAIN/webhook/whatsapp
    Verify token:  قيمة META_VERIFY_TOKEN في $BOT_ENV
    اشترك في الحقل: messages
  لتحديث الأسعار: عدّل $MENU ثم أعد تشغيل نفس الأمر.
EOF
