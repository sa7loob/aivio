#!/usr/bin/env bash
# تجربة شاملة لقالب «محل حلويات» على n8n حقيقي: Caddy + Postgres + n8n + بديل محلي لـ Meta و OpenAI.
# لا تحتاج حساب Meta ولا مفتاح OpenAI. تعمل في نسخة مؤقتة من agency/ على المنافذ 18080 و 18443
# (لا تتعارض مع Stack حقيقي)، وتحذف كل شيء في النهاية (KEEP=1 لإبقائه للفحص).
#
# الاستخدام:  agency/dev/e2e.sh        (يحتاج docker و openssl و python3 و curl)
set -euo pipefail
AGENCY=$(cd "$(dirname "$0")/.." && pwd)
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-agencye2e}
WORK=${WORK:-$(mktemp -d)}
CLIENT=shahrazad
HTTPS_PORT=18443
OWNER=218910000001
SECRET=e2e-app-secret
MOCK="$COMPOSE_PROJECT_NAME-mock"
N8N_IMAGE=$(grep -m1 -oE 'n8nio/n8n:[0-9.]+' "$AGENCY/docker-compose.yml")
PASS=0; FAIL=0; FAILED=()

cleanup() {
  [ "${KEEP:-0}" = 1 ] && { echo "KEEP=1: النسخة في $WORK (المشروع $COMPOSE_PROJECT_NAME)"; return; }
  docker rm -f "$MOCK" >/dev/null 2>&1 || true
  (cd "$WORK" && docker compose down -v >/dev/null 2>&1) || true
  docker run --rm --user 0 -v "$WORK:/w" --entrypoint rm "$N8N_IMAGE" -rf /w/data >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
check() {   # check "<وصف>" <أمر...>
  local desc=$1; shift
  if "$@" >/dev/null 2>&1; then PASS=$((PASS + 1)); printf '  \033[32m✔\033[0m %s\n' "$desc"
  else FAIL=$((FAIL + 1)); FAILED+=("$desc"); printf '  \033[31m✘ %s\033[0m\n' "$desc"; fi
}
wait_until() {   # wait_until <ثوانٍ> <أمر...>
  local limit=$1; shift
  for _ in $(seq 1 "$limit"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}

# ---- أدوات: Meta webhook موقّع، البديل، قاعدة العميل
R=(-k -s --resolve "$CLIENT.localhost:$HTTPS_PORT:127.0.0.1")
URL="https://$CLIENT.localhost:$HTTPS_PORT/webhook/whatsapp"
wa_post() {   # wa_post <json> [secret]
  local sig
  sig=$(printf '%s' "$1" | openssl dgst -sha256 -hmac "${2:-$SECRET}" | sed 's/^.*= //')
  curl "${R[@]}" -o /dev/null -w '%{http_code}' -X POST "$URL" -H 'Content-Type: application/json' \
    -H "X-Hub-Signature-256: sha256=$sig" --data-binary "$1"
}
wa_body() {   # wa_body <from> <message-json>
  printf '{"object":"whatsapp_business_account","entry":[{"id":"WABA1","changes":[{"field":"messages","value":{"messaging_product":"whatsapp","metadata":{"display_phone_number":"218910000000","phone_number_id":"PN-SHZ-1"},"contacts":[{"profile":{"name":"أم علي"},"wa_id":"%s"}],"messages":[%s]}}]}]}' "$1" "$2"
}
text_msg() { printf '{"from":"%s","id":"%s","timestamp":"%s","type":"text","text":{"body":"%s"}}' "$1" "$2" "$(date +%s)" "$3"; }
audio_msg() { printf '{"from":"%s","id":"%s","timestamp":"%s","type":"audio","audio":{"mime_type":"audio/ogg; codecs=opus","id":"%s","voice":true}}' "$1" "$2" "$(date +%s)" "$3"; }
wa_text() { wa_post "$(wa_body "$1" "$(text_msg "$1" "$2" "$3")")" >/dev/null; }
wa_audio() { wa_post "$(wa_body "$1" "$(audio_msg "$1" "$2" "$3")")" >/dev/null; }
mock_script() { docker exec "$MOCK" wget -qO- --post-data="$1" http://127.0.0.1:8080/__mock/script >/dev/null; }
# mq '<تعبير python على R = قائمة طلبات البديل>'
mq() { docker exec "$MOCK" wget -qO- http://127.0.0.1:8080/__mock/requests | python3 -c "
import json, sys
R = json.load(sys.stdin)
def sends(to): return [r['message'] for r in R if r['kind'] == 'send' and r['message']['to'] == to]
def chats(phone): return [r['request'] for r in R if r['kind'] == 'chat' and ('+' + phone) in r['request']['messages'][0]['content']]
def users(req): return [m['content'] for m in req['messages'] if m['role'] == 'user']
print($1)"; }
mtrue() { [ "$(mq "$1")" = True ]; }
db() { (cd "$WORK" && docker compose exec -T postgres psql -U "$CLIENT" -d "$CLIENT" -At -c "$1"); }
dbis() { [ "$(db "$1")" = "$2" ]; }
verify_ok() { curl "${R[@]}" "$VERIFY" | grep -qx 4242; }
verify_code() { [ "$(curl "${R[@]}" -o /dev/null -w '%{http_code}' "$URL?hub.mode=subscribe&hub.verify_token=$1&hub.challenge=1")" = "$2" ]; }
reply_json() {   # reply_json <reply> <order-json|none> [handoff-reason]
  local order=$2 handoff='{"needed":false,"reason":null}'
  [ "$order" = none ] && order='{"status":"none","customer_name":null,"phone":null,"items":[],"fulfillment":null,"address":null,"needed_at":null,"notes":null}'
  [ -n "${3:-}" ] && handoff="{\"needed\":true,\"reason\":\"$3\"}"
  printf '{"reply":"%s","order":%s,"handoff":%s}' "$1" "$order" "$handoff"
}
ORDER='{"status":"complete","customer_name":"أم علي","phone":null,"items":[{"product":"بقلاوة بالفستق","quantity":1.5,"unit":"كيلو","note":null},{"product":"تورتة عيد ميلاد صغيرة","quantity":1,"unit":"تورتة","note":"مكتوب عليها سارة"}],"fulfillment":"delivery","address":"حي الأندلس","needed_at":"الخميس العصر","notes":null}'

step "تجهيز نسخة مؤقتة: $WORK"
mkdir -p "$WORK"
tar -C "$AGENCY" --exclude=./data --exclude=./backups --exclude=./.env --exclude='./clients/*' -cf - . | tar -C "$WORK" -xf -
cd "$WORK"
sed -i -e 's/"80:80"/"18080:80"/' -e "s/\"443:443\"/\"$HTTPS_PORT:443\"/" -e "s#\"443:443/udp\"#\"$HTTPS_PORT:443/udp\"#" docker-compose.yml
HASH=$(docker run --rm caddy:2.11 caddy hash-password --plaintext e2e-editor-pass)
cat > .env <<EOF
BASE_DOMAIN=localhost
ACME_EMAIL=e2e@example.com
POSTGRES_PASSWORD=$(openssl rand -hex 16)
EDITOR_USER=agency
EDITOR_HASH='$HASH'
TZ=Africa/Tripoli
EXECUTIONS_MAX_AGE_HOURS=336
N8N_MEM_LIMIT=1g
EOF
docker compose up -d --wait >/dev/null 2>&1
./scripts/new-client.sh "$CLIENT" | grep -E "✔"
docker run -d --name "$MOCK" --network "${COMPOSE_PROJECT_NAME}_default" --network-alias mock \
  -v "$WORK/dev:/mockdir:ro" --entrypoint node "$N8N_IMAGE" /mockdir/mock-upstream.js >/dev/null
cp templates/sweets/menu.example.csv "clients/$CLIENT.menu.csv"
sed -e 's#^PHONE_NUMBER_ID=.*#PHONE_NUMBER_ID=PN-SHZ-1#' -e "s#^META_APP_SECRET=.*#META_APP_SECRET=$SECRET#" \
    -e 's#^META_VERIFY_TOKEN=.*#META_VERIFY_TOKEN=e2e-verify#' -e 's#^WHATSAPP_TOKEN=.*#WHATSAPP_TOKEN=test-wa-token#' \
    -e 's#^OPENAI_API_KEY=.*#OPENAI_API_KEY=sk-test#' -e 's#^OWNER_PHONE=.*#OWNER_PHONE="+218 91 000 0001"#' \
    -e 's#^GRAPH_BASE_URL=.*#GRAPH_BASE_URL=http://mock:8080/graph/v23.0#' \
    -e 's#^OPENAI_BASE_URL=.*#OPENAI_BASE_URL=http://mock:8080/openai/v1#' \
    templates/sweets/bot.example.env > "clients/$CLIENT.bot.env"
./scripts/deploy-bot.sh "$CLIENT" sweets | grep -E "✔|صنف"
VERIFY="$URL?hub.mode=subscribe&hub.verify_token=e2e-verify&hub.challenge=4242"
wait_until 60 verify_ok || true

step "1) ربط Meta"
check "التوكن الصحيح يعيد hub.challenge" verify_ok
check "توكن خاطئ = 403" verify_code wrong 403

step "2) رسالة نصية"
P=218920000002
wa_text $P wamid.S2 'السلام عليكم، قداش كيلو البقلاوة؟'
wait_until 30 mtrue "len(sends('$P')) == 1" || true
check "رد واحد للزبون" mtrue "len(sends('$P')) == 1 and sends('$P')[0]['text']['body'].startswith('مرحبا بيك')"
check "OpenAI استلم القائمة بالأسعار ورسالة الزبون" mtrue "'بقلاوة بالفستق: 85 د.ل لكل كيلو' in chats('$P')[0]['messages'][0]['content'] and users(chats('$P')[0]) == ['السلام عليكم، قداش كيلو البقلاوة؟']"
check "JSON منظم (strict) بالنموذج المحدد" mtrue "chats('$P')[0]['response_format']['json_schema']['strict'] and chats('$P')[0]['model'] == 'gpt-4o'"
check "الرسالتان محفوظتان (الصادرة بمعرّف Meta)" dbis "SELECT string_agg(direction || ':' || (wa_message_id LIKE 'wamid.%')::text, ',' ORDER BY m.id) FROM messages m JOIN contacts c ON c.id = m.contact_id WHERE c.wa_id = '$P'" "in:true,out:true"
check "الـ credentials مربوطة (لا طلب بدون توكن صحيح)" mtrue "not any(r['kind'].endswith('unauthorized') for r in R)"

step "3) تكرار نفس الرسالة من Meta"
wa_text $P wamid.S2 'السلام عليكم، قداش كيلو البقلاوة؟'
sleep 8
check "لا رد ثانٍ" mtrue "len(sends('$P')) == 1 and len(chats('$P')) == 1"
check "لا حفظ مكرر" dbis "SELECT count(*) FROM messages WHERE wa_message_id = 'wamid.S2'" 1

step "4) توقيع خاطئ"
P4=218920000004
wa_post "$(wa_body $P4 "$(text_msg $P4 wamid.S4 'test')")" wrong-secret >/dev/null
curl "${R[@]}" -o /dev/null -X POST "$URL" -H 'Content-Type: application/json' --data-binary "$(wa_body $P4 "$(text_msg $P4 wamid.S4b x)")"
sleep 8
check "لا حفظ" dbis "SELECT count(*) FROM messages WHERE wa_message_id IN ('wamid.S4', 'wamid.S4b')" 0
check "لا OpenAI ولا رد" mtrue "len(sends('$P4')) + len(chats('$P4')) == 0"

step "5) رسالة صوتية"
P5=218920000005
mock_script '{"transcript":"نبي كيلو ونص بقلاوة بالفستق للخميس"}'
wa_audio $P5 wamid.S5 MEDIA-S5
wait_until 40 mtrue "len(sends('$P5')) == 1" || true
check "تنزيل الصوت بتوكن واتساب" mtrue "[r['id'] for r in R if r['kind'] == 'media_url'] == ['MEDIA-S5'] and any(r['kind'] == 'media_download' for r in R)"
check "تفريغ: gpt-4o-transcribe، عربي، تلميحات بأسماء الأصناف، ملف .ogg" mtrue "(lambda f: f['model'] == 'gpt-4o-transcribe' and f['language'] == 'ar' and 'بقلاوة بالفستق' in f['prompt'] and f['file']['filename'] == 'voice.ogg')([r['fields'] for r in R if r['kind'] == 'transcription'][0])"
check "النص محفوظ في الرسالة" dbis "SELECT text FROM messages WHERE wa_message_id = 'wamid.S5'" "نبي كيلو ونص بقلاوة بالفستق للخميس"
check "الوكيل رأى الصوت مكتوباً" mtrue "users(chats('$P5')[0]) == ['[رسالة صوتية: نبي كيلو ونص بقلاوة بالفستق للخميس]']"

step "6) رسالة صوتية لا يمكن تنزيلها"
P6=218920000006
wa_audio $P6 wamid.S6 MEDIA-expired-1
wait_until 40 mtrue "len(sends('$P6')) == 1" || true
check "الرسالة معلّمة كفاشلة والبوت رد بدون انتظار" dbis "SELECT media_failed FROM messages WHERE wa_message_id = 'wamid.S6'" t
check "الوكيل يعرف أن الصوت ما وضحش" mtrue "users(chats('$P6')[0]) == ['[رسالة صوتية ما وضحتش]']"

step "7) رسالتان متتاليتان = رد واحد"
P7=218920000007
wa_text $P7 wamid.S7a 'نبي نطلب'
sleep 1
wa_text $P7 wamid.S7b 'تورتة صغيرة للسبت'
wait_until 30 mtrue "len(sends('$P7')) >= 1" || true
sleep 6
check "طلب واحد لـ OpenAI فيه الرسالتان، ورد واحد" mtrue "len(chats('$P7')) == 1 and users(chats('$P7')[0]) == ['نبي نطلب', 'تورتة صغيرة للسبت'] and len(sends('$P7')) == 1"

step "8) صوت ثم نص بسرعة: البوت ينتظر التفريغ"
P8=218920000008
mock_script '{"transcript":"عندكم معمول بالتمر؟","transcript_delay_ms":7000}'
wa_audio $P8 wamid.S8a MEDIA-S8
sleep 1
wa_text $P8 wamid.S8b 'ونبي نص كيلو غريبة'
wait_until 60 mtrue "len(sends('$P8')) >= 1" || true
sleep 5
mock_script '{"transcript_delay_ms":0}'
check "رد واحد بعد التفريغ، والوكيل رأى الصوت والنص بالترتيب" mtrue "len(chats('$P8')) == 1 and users(chats('$P8')[0]) == ['[رسالة صوتية: عندكم معمول بالتمر؟]', 'ونبي نص كيلو غريبة'] and len(sends('$P8')) == 1"

step "9) طلب مكتمل = تنبيه صاحب المحل مرة واحدة"
P9=218920000009
mock_script "{\"completions\":[$(reply_json 'تمام، طلبك وصل للمحل وحيتواصلوا معاك' "$ORDER")]}"
wa_text $P9 wamid.S9 'باهي أكد الطلب'
wait_until 40 mtrue "len(sends('$OWNER')) >= 1" || true
check "الطلب محفوظ بالأصناف والمجموع التقريبي (1.5×85 + 120)" dbis "SELECT jsonb_array_length(o.items) || '|' || o.estimated_total_lyd || '|' || o.fulfillment || '|' || (o.notified_at IS NOT NULL) FROM orders o JOIN contacts c ON c.id = o.contact_id WHERE c.wa_id = '$P9'" "2|247.500|delivery|true"
check "رسالة المالك فيها الأصناف والتوصيل ورابط الزبون" mtrue "(lambda b: 'طلب جديد — حلويات شهرزاد' in b and '• بقلاوة بالفستق × 1.5 كيلو' in b and '🚚 توصيل: حي الأندلس' in b and 'wa.me/$P9' in b)(sends('$OWNER')[0]['text']['body'])"
mock_script "{\"completions\":[$(reply_json 'العفو، نورتنا' "$ORDER")]}"
wa_text $P9 wamid.S9b 'يعطيك الصحة'
wait_until 30 mtrue "len(sends('$P9')) == 2" || true
sleep 3
check "نفس الطلب مرة ثانية: لا طلب جديد" dbis "SELECT count(*) FROM orders o JOIN contacts c ON c.id = o.contact_id WHERE c.wa_id = '$P9'" 1
check "نفس الطلب مرة ثانية: لا تنبيه ثانٍ" mtrue "len(sends('$OWNER')) == 1"

step "10) زبون يحتاج رد بشري"
P10=218920000010
mock_script "{\"completions\":[$(reply_json 'تو حد من المحل يتواصل معاك' none 'يبي تورتة بتصميم خاص')]}"
wa_text $P10 wamid.S10 'نبي تورتة على شكل سيارة'
wait_until 40 mtrue "len(sends('$OWNER')) >= 2" || true
check "تنبيه المالك بالسبب ورقم الزبون" mtrue "(lambda b: 'زبون يحتاج رد منكم' in b and 'السبب: يبي تورتة بتصميم خاص' in b and '+$P10' in b)(sends('$OWNER')[-1]['text']['body'])"

step "11) تنبيه المالك بقالب معتمد"
db "UPDATE bot_settings SET owner_template_name = 'new_order'" >/dev/null
P11=218920000011
mock_script "{\"completions\":[$(reply_json 'تمام' "${ORDER/الخميس العصر/الجمعة}")]}"
wa_text $P11 wamid.S11 'أكد'
wait_until 40 mtrue "len(sends('$OWNER')) >= 3" || true
check "قالب new_order بـ 4 متغيرات في سطر واحد" mtrue "(lambda m: m['type'] == 'template' and m['template']['name'] == 'new_order' and len(m['template']['components'][0]['parameters']) == 4 and all('\n' not in p['text'] for p in m['template']['components'][0]['parameters']))(sends('$OWNER')[-1])"
db "UPDATE bot_settings SET owner_template_name = NULL" >/dev/null

step "12) تعطل OpenAI"
P12=218920000012
mock_script '{"fail_chat":true}'
wa_text $P12 wamid.S12 'قداش الغريبة؟'
wait_until 60 mtrue "len(sends('$P12')) == 1" || true
mock_script '{"fail_chat":false}'
sleep 3
check "3 محاولات ثم اعتذار قصير للزبون" mtrue "len(chats('$P12')) == 3 and sends('$P12')[0]['text']['body'].startswith('معليش')"
check "تنبيه المالك بالعطل" mtrue "'تعطل الاتصال بالذكاء الاصطناعي' in sends('$OWNER')[-1]['text']['body']"

step "13) تعطل إرسال واتساب = تسجيل الخطأ"
P13=218920000013
mock_script '{"fail_sends":true}'
wa_text $P13 wamid.S13 'مرحبا'
wait_until 60 dbis "SELECT count(*) > 0 FROM workflow_errors WHERE node = 'Send reply'" t || true
mock_script '{"fail_sends":false}'
check "workflow الأخطاء سجّل العقدة والرسالة" dbis "SELECT count(*) > 0 FROM workflow_errors WHERE node = 'Send reply' AND workflow = '[WA] رسائل واتساب' AND message <> ''" t
check "3 محاولات إرسال" mtrue "len(sends('$P13')) == 3"

step "14) التنظيف اليومي"
db "INSERT INTO messages (contact_id, direction, type, text, created_at) SELECT id, 'in', 'text', 'قديمة', now() - interval '100 days' FROM contacts WHERE wa_id = '$P'" >/dev/null
before=$(db "SELECT count(*) FROM messages")
# n8n execute يشغّل task broker خاصاً به: منفذ مختلف عن النسخة العاملة
docker compose exec -T -e N8N_RUNNERS_BROKER_PORT=5690 "n8n-$CLIENT" n8n execute --id=botCleanupFlow01 >/dev/null 2>&1 || true
check "حذف الرسالة الأقدم من history_days" dbis "SELECT count(*) FROM messages WHERE text = 'قديمة'" 0
check "الرسائل الحديثة باقية" dbis "SELECT count(*) FROM messages" $((before - 1))

step "15) إعادة النشر"
./scripts/deploy-bot.sh "$CLIENT" sweets >/dev/null 2>&1
wait_until 60 verify_ok || true
check "الإعدادات صف واحد" dbis "SELECT count(*) FROM bot_settings" 1
check "القائمة بدون تكرار" dbis "SELECT count(*) FROM products" 11
P15=218920000015
wa_text $P15 wamid.S15 'مرحبا'
check "البوت يرد بعد إعادة النشر" wait_until 30 mtrue "len(sends('$P15')) == 1"

printf '\n\033[1mالنتيجة: %d ناجح، %d فاشل\033[0m\n' "$PASS" "$FAIL"
for f in "${FAILED[@]}"; do echo "  ✘ $f"; done
[ "$FAIL" -eq 0 ]
