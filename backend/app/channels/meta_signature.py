"""Meta webhook signature verification (X-Hub-Signature-256)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

_PREFIX = "sha256="


def compute_signature(raw_body: bytes, app_secret: str) -> str:
    return _PREFIX + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def verify_meta_signature(raw_body: bytes, header_value: str | None, app_secret: str) -> bool:
    """يجب تمرير البايتات كما وصلت تماماً (قبل json.loads).
    أي إعادة تسلسل للـ JSON تغيّر البايتات (مثل \\u escapes التي ترسلها Meta) فيفشل التوقيع."""
    if not header_value or not app_secret or not header_value.startswith(_PREFIX):
        return False
    expected = compute_signature(raw_body, app_secret)
    return hmac.compare_digest(expected, header_value.strip().lower())


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def parse_signed_request(signed_request: str | None, app_secret: str) -> dict[str, Any] | None:
    """signed_request من Meta (Deauthorize / Data Deletion callbacks):
    '<b64url(HMAC-SHA256(payload_b64, app_secret))>.<b64url(json payload)>'.
    يعيد الـ payload (فيه user_id) أو None إذا كان التوقيع أو الصيغة غير صالحين."""
    if not signed_request or not app_secret or signed_request.count(".") != 1:
        return None
    sig_b64, payload_b64 = signed_request.split(".", 1)
    try:
        signature = _b64url_decode(sig_b64)
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or str(payload.get("algorithm", "")).upper() != "HMAC-SHA256":
        return None
    expected = hmac.new(app_secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return payload if hmac.compare_digest(signature, expected) else None
