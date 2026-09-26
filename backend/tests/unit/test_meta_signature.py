from app.channels.meta_signature import compute_signature, verify_meta_signature

SECRET = "s3cret"
BODY = '{"object":"whatsapp_business_account","x":"\\u0642\\u062f\\u0627\\u0634"}'.encode()


def test_valid_signature():
    assert verify_meta_signature(BODY, compute_signature(BODY, SECRET), SECRET)


def test_rejects_wrong_secret():
    assert not verify_meta_signature(BODY, compute_signature(BODY, "other"), SECRET)


def test_rejects_tampered_body():
    sig = compute_signature(BODY, SECRET)
    assert not verify_meta_signature(BODY + b" ", sig, SECRET)


def test_rejects_missing_or_malformed_header():
    assert not verify_meta_signature(BODY, None, SECRET)
    assert not verify_meta_signature(BODY, "", SECRET)
    assert not verify_meta_signature(BODY, "sha1=abc", SECRET)
    assert not verify_meta_signature(BODY, compute_signature(BODY, SECRET)[7:], SECRET)


def test_reserialized_json_breaks_signature():
    """لهذا نتحقق على raw bytes: Meta ترسل \\u escapes، وإعادة التسلسل تغيّر البايتات."""
    import json
    sig = compute_signature(BODY, SECRET)
    reserialized = json.dumps(json.loads(BODY), ensure_ascii=False, separators=(",", ":")).encode()
    assert not verify_meta_signature(reserialized, sig, SECRET)


# ------------------------------------------------------------------ signed_request (Deauthorize / Data Deletion)
import base64 as _b64
import hashlib as _hashlib
import hmac as _hmac
import json as _json

from app.channels.meta_signature import parse_signed_request


def _signed(payload: dict, secret: str) -> str:
    p = _b64.urlsafe_b64encode(_json.dumps(payload).encode()).decode().rstrip("=")
    sig = _b64.urlsafe_b64encode(_hmac.new(secret.encode(), p.encode(), _hashlib.sha256).digest()).decode().rstrip("=")
    return f"{sig}.{p}"


def test_signed_request_valid():
    sr = _signed({"algorithm": "HMAC-SHA256", "user_id": "12345", "issued_at": 1790000000}, SECRET)
    assert parse_signed_request(sr, SECRET)["user_id"] == "12345"


def test_signed_request_rejects_tampering_and_garbage():
    sr = _signed({"algorithm": "HMAC-SHA256", "user_id": "12345"}, SECRET)
    sig, payload = sr.split(".")
    forged = _b64.urlsafe_b64encode(_json.dumps({"algorithm": "HMAC-SHA256", "user_id": "999"}).encode()).decode().rstrip("=")
    assert parse_signed_request(f"{sig}.{forged}", SECRET) is None
    assert parse_signed_request(sr, "other-secret") is None
    assert parse_signed_request(_signed({"algorithm": "none", "user_id": "1"}, SECRET), SECRET) is None
    for bad in (None, "", "abc", "a.b.c", "!!!.???"):
        assert parse_signed_request(bad, SECRET) is None
