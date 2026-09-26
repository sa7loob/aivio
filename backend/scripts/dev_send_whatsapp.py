"""يرسل webhook واتساب موقّعاً (رسالة زبون) للـ API المحلي — للتطوير والتجربة فقط.

    python -m scripts.dev_send_whatsapp --from 218913334444 --name "أبو محمد" \
        --text "قداش عمرة رمضان؟" --phone-number-id 100000000001

التوقيع بـ META_APP_SECRET (من البيئة أو .env)، فيمر بنفس مسار Meta الحقيقي:
التحقق من التوقيع => webhook_events => الـ worker.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
import urllib.request
import uuid

from app.core.config import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="sender", required=True, help="wa_id للزبون، مثال 218913334444")
    ap.add_argument("--name", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--phone-number-id", required=True, help="رقم القناة المسجّل (channel external_id)")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    args = ap.parse_args()

    payload = {"object": "whatsapp_business_account", "entry": [{"id": "dev-waba", "changes": [{
        "field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": args.phone_number_id, "phone_number_id": args.phone_number_id},
            "contacts": [{"profile": {"name": args.name}, "wa_id": args.sender}],
            "messages": [{"from": args.sender, "id": f"wamid.DEV{uuid.uuid4().hex}",
                          "timestamp": str(int(time.time())), "type": "text", "text": {"body": args.text}}]}}]}]}
    raw = json.dumps(payload, ensure_ascii=False).encode()
    secret = get_settings().meta_app_secret.get_secret_value().encode()
    sig = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{args.api}/webhooks/meta", data=raw, method="POST",
                                 headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    with urllib.request.urlopen(req) as resp:
        print(resp.status, resp.read().decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
