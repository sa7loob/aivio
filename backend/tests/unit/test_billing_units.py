import asyncio

import httpx
import pytest

from app.billing.vouchers import (
    CODE_LENGTH,
    format_code,
    generate_code,
    hash_code,
    luhn_valid,
    normalize_code,
)
from app.channels.meta_graph import PAGE_WEBHOOK_FIELDS, MetaGraphClient, MetaGraphError


# ------------------------------------------------------------------ vouchers
def test_generated_codes_are_valid_and_unique():
    codes = {generate_code() for _ in range(2000)}
    assert len(codes) == 2000
    assert all(len(c) == CODE_LENGTH and luhn_valid(c) for c in codes)


def test_luhn_catches_typos():
    code = generate_code()
    wrong_digit = code[:5] + str((int(code[5]) + 1) % 10) + code[6:]
    assert not luhn_valid(wrong_digit)
    i = next(i for i in range(CODE_LENGTH - 1) if code[i] != code[i + 1])
    swapped = code[:i] + code[i + 1] + code[i] + code[i + 2:]
    # Luhn يكشف تبديل رقمين متجاورين إلا الحالة 09/90
    if {code[i], code[i + 1]} != {"0", "9"}:
        assert not luhn_valid(swapped)


def test_normalize_accepts_customer_input():
    code = generate_code()
    pretty = format_code(code)
    assert pretty.count("-") == 3
    arabic = pretty.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    assert normalize_code(pretty) == code
    assert normalize_code(arabic.replace("-", " ")) == code
    assert normalize_code(code[:-1]) is None
    assert normalize_code(code[:-1] + str((int(code[-1]) + 1) % 10)) is None
    assert normalize_code("") is None


def test_hash_is_peppered_and_stable():
    code = generate_code()
    assert hash_code(code, "p1") == hash_code(code, "p1")
    assert hash_code(code, "p1") != hash_code(code, "p2")
    assert code not in hash_code(code, "p1")


# ------------------------------------------------------------------ Meta Graph (assisted onboarding)
def _client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MetaGraphClient(http, base_url="https://graph.test", api_version="v25.0"), http


def test_subscribe_page_sends_fields_and_token():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"] = str(req.url), req.headers["Authorization"]
        return httpx.Response(200, json={"success": True})

    async def go():
        g, http = _client(handler)
        async with http:
            return await g.subscribe_page("12345", "PAGE_TOKEN")

    assert asyncio.run(go()) == {"success": True}
    assert seen["url"].startswith("https://graph.test/v25.0/12345/subscribed_apps?")
    assert "subscribed_fields=" in seen["url"] and "message_echoes" in PAGE_WEBHOOK_FIELDS
    assert seen["auth"] == "Bearer PAGE_TOKEN"


def test_graph_errors_carry_meta_code():
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token", "code": 190}})

    async def go():
        g, http = _client(handler)
        async with http:
            await g.get_phone_number("111111", "BAD")

    with pytest.raises(MetaGraphError) as ei:
        asyncio.run(go())
    assert ei.value.code == 190 and ei.value.retryable is False


def test_debug_token_returns_data():
    def handler(req):
        assert "input_token=USER_TOKEN" in str(req.url)
        return httpx.Response(200, json={"data": {"is_valid": True, "expires_at": 0, "scopes": ["x"]}})

    async def go():
        g, http = _client(handler)
        async with http:
            return await g.debug_token("USER_TOKEN", "APPID|SECRET")

    assert asyncio.run(go())["is_valid"] is True
