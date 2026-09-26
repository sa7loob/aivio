import asyncio
import json

import httpx
import pytest

from app.channels.base import ChannelSendError
from app.channels.messenger import (
    BOT_ECHO_METADATA,
    MessengerClient,
    is_own_echo,
    parse_instagram,
    parse_messenger,
)
from app.channels.registry import parser_for
from app.channels.whatsapp import parse_webhook as parse_whatsapp

PAGE = "1111111111"


def _page_payload(*events, obj="page", account=PAGE):
    return {"object": obj, "entry": [{"id": account, "time": 1790000000000, "messaging": list(events)}]}


def _msg(sender, recipient, mid, **message):
    return {"sender": {"id": sender}, "recipient": {"id": recipient}, "timestamp": 1790000000123,
            "message": {"mid": mid, **message}}


def test_messenger_text_attachment_postback_and_noise():
    payload = _page_payload(
        _msg("PSID1", PAGE, "m1", text="قداش عمرة المولد؟"),
        _msg("PSID1", PAGE, "m2", attachments=[{"type": "image", "payload": {"url": "https://x"}}]),
        {"sender": {"id": "PSID1"}, "recipient": {"id": PAGE}, "timestamp": 1790000000200,
         "postback": {"mid": "m3", "title": "الأسعار", "payload": "PRICES"}},
        {"sender": {"id": "PSID1"}, "recipient": {"id": PAGE}, "read": {"watermark": 1}},
        _msg("PSID1", PAGE, "m4", is_deleted=True),
    )
    parsed = parse_messenger(payload)
    assert [m.external_message_id for m in parsed.messages] == ["m1", "m2", "m3"]
    m1, m2, m3 = parsed.messages
    assert (m1.channel, m1.account_external_id, m1.user_external_id) == ("messenger", PAGE, "PSID1")
    assert m1.text == "قداش عمرة المولد؟" and m1.timestamp.year == 2026
    assert m2.type == "image" and m3.type == "interactive" and m3.text == "الأسعار"
    assert parsed.skipped == 1            # المحذوفة؛ read لا يُعد خطأ
    assert not any(m.is_echo for m in parsed.messages)


def test_echo_user_is_recipient_and_own_echo_detection():
    staff = _msg(PAGE, "PSID1", "e1", text="أهلاً، أنا سالم من المكتب", is_echo=True, app_id=263902037430900)
    bot = _msg(PAGE, "PSID1", "e2", text="رد البوت", is_echo=True, metadata=BOT_ECHO_METADATA)
    ours_by_app = _msg(PAGE, "PSID1", "e3", text="x", is_echo=True, app_id=555)
    parsed = parse_messenger(_page_payload(staff, bot, ours_by_app))
    e1, e2, e3 = parsed.messages
    assert e1.is_echo and e1.user_external_id == "PSID1"
    assert not is_own_echo(e1, "555")               # Page Inbox (تطبيق آخر) => موظف
    assert is_own_echo(e2, None)                     # metadata الخاصة بالبوت
    assert is_own_echo(e3, "555") and not is_own_echo(e3, None)


def test_instagram_parser_and_registry():
    payload = _page_payload(_msg("IGSID9", "IG1", "ig1", text="كم سعر الحج؟"), obj="instagram", account="IG1")
    parsed = parse_instagram(payload)
    assert parsed.messages[0].channel == "instagram" and parsed.messages[0].user_external_id == "IGSID9"
    assert parse_messenger(payload).messages == []       # object مختلف
    assert parser_for("page") is parse_messenger and parser_for("instagram") is parse_instagram


def test_whatsapp_business_app_echoes(load_fixture):
    payload = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [
        {"field": "smb_message_echoes", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "218910000000", "phone_number_id": "PNID"},
            "message_echoes": [
                {"from": "218910000000", "to": "218913334444", "id": "wamid.E1", "timestamp": "1790000000",
                 "type": "text", "text": {"body": "مرحبا، معاك المكتب"}},
                {"from": "218910000000", "to": "218913334444", "id": "wamid.E2", "timestamp": "1790000001",
                 "type": "revoke"}]}},
        {"field": "history", "value": {"metadata": {"phone_number_id": "PNID"}}}]}]}
    parsed = parse_whatsapp(payload)
    [echo] = parsed.messages
    assert echo.is_echo and echo.user_external_id == "218913334444" and echo.text == "مرحبا، معاك المكتب"
    assert echo.user_phone_e164 == "+218913334444"
    assert parsed.skipped == 2            # revoke + history


# ------------------------------------------------------------------ sender
def _client(handler, channel):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MessengerClient(http, base_url="https://graph.test", api_version="v25.0", channel=channel), http


def _send(handler, channel, body="مرحبا بيك"):
    async def go():
        c, http = _client(handler, channel)
        async with http:
            return await c.send_text(account_external_id=PAGE, access_token="PT", to="PSID1", body=body)
    return asyncio.run(go())


def test_send_messenger_and_instagram_payloads():
    seen = []

    def handler(req):
        seen.append((str(req.url), req.headers["Authorization"], json.loads(req.content)))
        return httpx.Response(200, json={"recipient_id": "PSID1", "message_id": "mid.OUT"})

    assert _send(handler, "messenger").external_message_id == "mid.OUT"
    _send(handler, "instagram", body="x" * 1500)
    (url, auth, msg), (_, _, ig) = seen
    assert url == f"https://graph.test/v25.0/{PAGE}/messages" and auth == "Bearer PT"
    assert msg["messaging_type"] == "RESPONSE" and msg["recipient"] == {"id": "PSID1"}
    assert msg["message"]["metadata"] == BOT_ECHO_METADATA
    assert "metadata" not in ig["message"] and len(ig["message"]["text"]) == 1000


@pytest.mark.parametrize("status,code,retryable", [
    (400, 10, False), (400, 190, False), (400, 613, True), (500, None, True), (400, 551, False)])
def test_send_error_mapping(status, code, retryable):
    def handler(req):
        return httpx.Response(status, json={"error": {"message": "x", "code": code}})

    with pytest.raises(ChannelSendError) as ei:
        _send(handler, "messenger")
    assert ei.value.retryable is retryable


def test_templates_not_supported():
    async def go():
        c, http = _client(lambda r: httpx.Response(500), "messenger")
        async with http:
            await c.send_template(account_external_id=PAGE, access_token="t", to="x", name="n")
    with pytest.raises(ChannelSendError) as ei:
        asyncio.run(go())
    assert ei.value.retryable is False
