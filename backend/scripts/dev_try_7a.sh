#!/usr/bin/env bash
# تجربة المرحلة 7a محلياً (للتطوير فقط): رسالة صوتية => نص => رد البوت، بروشور => مسودة => اعتماد،
# رد موظف => معلومة للبوت. كل تشغيل ينشئ نشاطاً ورقماً جديدين.
#
# المتطلبات (انظر docs/architecture/10-phase-7-smart-sales-and-ingestion.md، قسم «التجربة»):
#   الـ API على :8000 والـ worker و scripts.dev_mock_upstream على :8099، وخطة التسجيل (starter) موجودة.
# الاستخدام (من مجلد backend):
#   ./scripts/dev_try_7a.sh                   # بروشور تجريبي مولَّد
#   ./scripts/dev_try_7a.sh ~/brochure.jpg    # بروشورك (JPG/PNG/WEBP/PDF)
set -euo pipefail
cd "$(dirname "$0")/.."

API=${API:-http://127.0.0.1:8000}
MOCK=${MOCK:-http://127.0.0.1:8099}
PY=${PYTHON:-.venv/bin/python}
RUN=$(date +%s)
PNID="9${RUN}0"                       # رقم القناة التجريبي (phone_number_id)
CUSTOMER="21891${RUN: -7}"            # زبون: 091xxxxxxx
SLUG="try7a-${RUN}"
O='Origin: http://localhost:3000'
J=$(mktemp); IMG_TMP=$(mktemp --suffix=.png); trap 'rm -f "$J" "$IMG_TMP"' EXIT

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
py() { python3 -c "import sys, json; d = json.load(sys.stdin); $1"; }
api() { curl -sS -b "$J" -H "X-Tenant-ID: $T" -H "$O" "$@"; }
wait_for() {   # wait_for <seconds> <command...>: يعيد المحاولة كل ثانية حتى ينجح الأمر
  local limit=$1; shift
  for _ in $(seq 1 "$limit"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "timeout: $*" >&2; return 1
}

step "1) نشاط تجريبي + رقم واتساب تجريبي"
curl -sSf -c "$J" -X POST "$API/api/v1/auth/register" -H "$O" -H 'Content-Type: application/json' -d \
  "{\"email\":\"$SLUG@example.ly\",\"password\":\"try-pass-123\",\"full_name\":\"صاحب التجربة\",\"business_name\":\"وكالة التجربة\",\"business_slug\":\"$SLUG\"}" >/dev/null
T=$(curl -sSf -b "$J" "$API/api/v1/me" | py "print(d['tenants'][0]['tenant_id'])")
(set -a; . ./.env; set +a
 "$PY" -m scripts.register_whatsapp_channel --tenant-slug "$SLUG" --tenant-name "وكالة التجربة" \
   --phone-number-id "$PNID" --display-name "+218 91 000 0009" --test)

step "2) رسالة صوتية (التفريغ يتأخر 6 ثوانٍ عمداً: البوت ينتظره)"
curl -sSf -X POST "$MOCK/__mock/transcript" -d '{"text":"السلام عليكم، قداش عمرة رمضان للعيلة؟ نبو نسافروا من طرابلس","delay_seconds":6}' >/dev/null
"$PY" -m scripts.dev_send_whatsapp --from "$CUSTOMER" --name "أبو محمد" --audio "MOCKMEDIA-$RUN" --phone-number-id "$PNID"
has_bot_reply() {
  C=$(api "$API/api/v1/conversations" | py "print(d['items'][0]['id'])") &&
  api "$API/api/v1/conversations/$C/messages" | py "assert any(m['sender_type'] == 'bot' for m in d['items'])"
}
wait_for 40 has_bot_reply
C=$(api "$API/api/v1/conversations" | py "print(d['items'][0]['id'])")
api "$API/api/v1/conversations/$C/messages" | py "
for m in d['items']: print(f\"  {m['created_at'][11:19]}  {m['sender_type']:8} {m['msg_type']:5}  {m['text_content']}\")"
curl -sSf -X POST "$MOCK/__mock/transcript" -d '{"delay_seconds":0}' >/dev/null

step "3) بروشور => برامج مسودة (لا يراها البوت قبل الاعتماد)"
IMG=${1:-}
if [ -z "$IMG" ]; then
  python3 - "$IMG_TMP" <<'EOF'
import struct, sys, zlib
w, h = 32, 24
raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(h))
chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
open(sys.argv[1], "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                              + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
EOF
  IMG=$IMG_TMP
fi
case "${IMG,,}" in *.pdf) CT=application/pdf ;; *.png) CT=image/png ;; *.webp) CT=image/webp ;; *) CT=image/jpeg ;; esac
IID=$(api -X POST "$API/api/v1/catalog/imports?filename=$(basename "$IMG")" -H "Content-Type: $CT" \
          --data-binary @"$IMG" | py "print(d['id'])")
import_finished() { api "$API/api/v1/catalog/imports/$IID" | py "assert d['status'] in ('done', 'failed')"; }
wait_for 90 import_finished
api "$API/api/v1/catalog/imports/$IID" | py "
print('  الحالة:', d['status'], '| برامج:', d['packages_created'], '| خطأ:', d['error'])
[print('  تحذير:', w) for w in d['warnings']]
[print('  مسودة:', p['title'], '| مواعيد', p['departures'], '| أسعار', p['prices'], '| من', p['price_from']) for p in d['packages']]"
PKG=$(api "$API/api/v1/catalog/imports/$IID" | py "
ok = [p for p in d['packages'] if p['price_from'] is not None]
print(ok[0]['id'] if ok else '')")

if [ -n "$PKG" ]; then
  step "4) مراجعة واعتماد أول برنامج"
  api "$API/api/v1/catalog/packages/$PKG" | py "
print('  ', d['title'], '|', d['departure_city'], '| يشمل:', '، '.join(d['includes']))
[print('   سعر', p['room_type'], p['traveler_type'], p['amount'], p['currency']) for p in d['prices']]"
  api -X POST "$API/api/v1/catalog/packages/$PKG/publish" | py "print('  ', d)"
  api "$API/api/v1/catalog/packages?status=active" | py "print('  البرامج المعتمدة:', [p['title'] for p in d])"
fi

step "5) رد موظف => معلومة للبوت"
"$PY" -m scripts.dev_send_whatsapp --from "$CUSTOMER" --name "أبو محمد" --text "هل تقبلوا الدفع بالتقسيط؟" --phone-number-id "$PNID" >/dev/null
api -X POST "$API/api/v1/conversations/$C/takeover" >/dev/null
sleep 2
api -X POST "$API/api/v1/conversations/$C/messages" -H 'Content-Type: application/json' \
  -d "{\"text\":\"نعم، نقبل التقسيط على دفعتين: نصف المبلغ عند الحجز والباقي قبل السفر بأسبوعين.\",\"client_msg_id\":\"$(python3 -c 'import uuid; print(uuid.uuid4())')\"}" >/dev/null
SUG=$(api "$API/api/v1/conversations/$C/knowledge-suggestion")
echo "$SUG" | py "print('  السؤال المقترح:', d['question']); print('  الجواب المقترح:', d['answer'])"
echo "$SUG" | api -X POST "$API/api/v1/conversations/$C/knowledge" -H 'Content-Type: application/json' --data-binary @- \
  | py "print('  حُفظت:', d)"
embedded() { api "$API/api/v1/knowledge?source=staff" | py "assert d and d[0]['embedded']"; }
wait_for 20 embedded
api "$API/api/v1/knowledge?source=staff" | py "[print('  معلومة:', k['title'], '| embedding:', k['embedded'], '| بواسطة', k['created_by_name']) for k in d]"
api -X POST "$API/api/v1/conversations/$C/release" >/dev/null

printf '\n\033[1m✔ انتهت التجربة\033[0m — النشاط: %s  (دخول اللوحة: %s@example.ly / try-pass-123)\n' "$SLUG" "$SLUG"
