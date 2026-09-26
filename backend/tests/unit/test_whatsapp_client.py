import asyncio
import json

import httpx
import pytest

from app.channels.base import ChannelSendError
from app.channels.whatsapp import WhatsAppClient


def _client(handler) -> tuple[WhatsAppClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WhatsAppClient(http, base_url="https://graph.test", api_version="v23.0"), http


def _run(coro):
    return asyncio.run(coro)


def test_send_text_success():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messaging_product": "whatsapp",
                                         "messages": [{"id": "wamid.OUT1"}]})

    async def go():
        wa, http = _client(handler)
        async with http:
            return await wa.send_text(account_external_id="PNID", access_token="TKN",
                                      to="218913334444", body="  مرحبا  ")

    res = _run(go())
    assert res.external_message_id == "wamid.OUT1"
    assert seen["url"] == "https://graph.test/v23.0/PNID/messages"
    assert seen["auth"] == "Bearer TKN"
    assert seen["body"]["type"] == "text"
    assert seen["body"]["text"] == {"preview_url": False, "body": "مرحبا"}


def test_send_template_payload():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.T"}]})

    async def go():
        wa, http = _client(handler)
        async with http:
            return await wa.send_template(account_external_id="PNID", access_token="T",
                                          to="218", name="new_lead", language_code="ar",
                                          body_params=["أبو محمد", "عمرة رمضان"])

    _run(go())
    tpl = seen["body"]["template"]
    assert tpl["name"] == "new_lead" and tpl["language"] == {"code": "ar"}
    assert [p["text"] for p in tpl["components"][0]["parameters"]] == ["أبو محمد", "عمرة رمضان"]


@pytest.mark.parametrize("status,code,retryable", [
    (400, 131047, False),   # خارج نافذة 24 ساعة
    (401, 190, False),      # توكن غير صالح
    (400, 131056, True),    # pair rate limit
    (429, 130429, True),
    (500, None, True),
    (400, 100, False),      # invalid parameter
])
def test_error_mapping(status, code, retryable):
    def handler(request):
        err = {"message": "boom", "code": code} if code else {"message": "boom"}
        return httpx.Response(status, json={"error": err})

    async def go():
        wa, http = _client(handler)
        async with http:
            await wa.send_text(account_external_id="P", access_token="T", to="1", body="x")

    with pytest.raises(ChannelSendError) as ei:
        _run(go())
    assert ei.value.retryable is retryable
    assert ei.value.http_status == status


def test_network_error_is_retryable():
    def handler(request):
        raise httpx.ConnectError("down")

    async def go():
        wa, http = _client(handler)
        async with http:
            await wa.send_text(account_external_id="P", access_token="T", to="1", body="x")

    with pytest.raises(ChannelSendError) as ei:
        _run(go())
    assert ei.value.retryable is True


def test_empty_body_rejected_without_http_call():
    def handler(request):
        raise AssertionError("should not be called")

    async def go():
        wa, http = _client(handler)
        async with http:
            await wa.send_text(account_external_id="P", access_token="T", to="1", body="   ")

    with pytest.raises(ChannelSendError) as ei:
        _run(go())
    assert ei.value.retryable is False
