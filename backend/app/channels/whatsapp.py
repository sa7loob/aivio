"""WhatsApp Business Cloud API adapter: webhook parser + message sender.

Webhook shape (object = "whatsapp_business_account"):
    entry[].changes[] (field="messages").value:
        metadata.phone_number_id   -> مفتاح التوجيه للوكالة
        contacts[]  {wa_id, profile.name}
        messages[]  {id (wamid), from, timestamp, type, text.body | interactive | button | image ...}
        statuses[]  {id, status, recipient_id, timestamp, errors[]}
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.channels.base import (
    ChannelSendError,
    InboundMessage,
    MessageType,
    ParsedWebhook,
    SendResult,
    StatusUpdate,
)

log = logging.getLogger(__name__)

OBJECT_TYPE = "whatsapp_business_account"
TEXT_MAX_CHARS = 4096
_E164_DIGITS = re.compile(r"^[1-9][0-9]{7,14}$")

_MEDIA_TYPES: dict[str, MessageType] = {
    "image": "image", "audio": "audio", "voice": "audio", "video": "video",
    "document": "document", "sticker": "image",
}


# =========================================================================== parser
def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc)


def _extract_text(msg: dict[str, Any]) -> tuple[MessageType, str | None]:
    mtype = msg.get("type")
    if mtype == "text":
        return "text", (msg.get("text") or {}).get("body")
    if mtype == "interactive":
        inter = msg.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return "interactive", reply.get("title")
    if mtype == "button":  # زر رد سريع في قالب
        return "interactive", (msg.get("button") or {}).get("text")
    if mtype in _MEDIA_TYPES:
        return _MEDIA_TYPES[mtype], (msg.get(mtype) or {}).get("caption")
    if mtype == "location":
        loc = msg.get("location") or {}
        label = " - ".join(x for x in (loc.get("name"), loc.get("address")) if x)
        return "location", label or None
    if mtype == "reaction":
        return "reaction", (msg.get("reaction") or {}).get("emoji")
    return "other", None  # unsupported / system / order ...


def parse_webhook(payload: dict[str, Any]) -> ParsedWebhook:
    """Parser دفاعي: عنصر مشوّه يُتخطّى ويُعدّ، ولا يُسقط باقي الحدث."""
    result = ParsedWebhook()
    if payload.get("object") != OBJECT_TYPE:
        return result

    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            field = (change or {}).get("field")
            if field not in ("messages", "smb_message_echoes"):
                # history / smb_app_state_sync / account_update ...: ليست رسائل محادثة
                result.skipped += 1
                continue
            value = change.get("value") or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            if not phone_number_id:
                result.skipped += 1
                continue

            if field == "smb_message_echoes":
                _parse_business_app_echoes(value, str(phone_number_id), result)
                continue

            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts") or [] if isinstance(c, dict)
            }

            for msg in value.get("messages") or []:
                try:
                    wa_id = str(msg["from"])
                    mtype, text = _extract_text(msg)
                    result.messages.append(InboundMessage(
                        channel="whatsapp",
                        account_external_id=str(phone_number_id),
                        user_external_id=wa_id,
                        external_message_id=str(msg["id"]),
                        type=mtype,
                        text=text,
                        timestamp=_ts(msg.get("timestamp")),
                        user_display_name=names.get(wa_id),
                        # wa_id في واتساب = رقم الهاتف دولياً بدون "+"
                        user_phone_e164=f"+{wa_id}" if _E164_DIGITS.match(wa_id) else None,
                        raw=msg,
                    ))
                except (KeyError, TypeError, ValueError) as exc:
                    result.skipped += 1
                    log.warning("whatsapp: skipped malformed message: %s", exc)

            for st in value.get("statuses") or []:
                try:
                    result.statuses.append(StatusUpdate(
                        channel="whatsapp",
                        account_external_id=str(phone_number_id),
                        external_message_id=str(st["id"]),
                        status=str(st.get("status")),
                        recipient_id=st.get("recipient_id"),
                        timestamp=_ts(st.get("timestamp")),
                        errors=st.get("errors") or [],
                    ))
                except (KeyError, TypeError, ValueError):
                    result.skipped += 1
    return result


def _parse_business_app_echoes(value: dict[str, Any], phone_number_id: str,
                               result: ParsedWebhook) -> None:
    """Coexistence: رسائل كتبها الموظف من تطبيق WhatsApp Business على هاتفه.
    from = رقم النشاط، to = الزبون. تُعامل كـ echo => استلام بشري للمحادثة."""
    for echo in value.get("message_echoes") or []:
        try:
            if echo.get("type") in ("revoke", "edit"):
                result.skipped += 1
                continue
            customer = str(echo["to"])
            mtype, text = _extract_text(echo)
            result.messages.append(InboundMessage(
                channel="whatsapp", account_external_id=phone_number_id,
                user_external_id=customer, external_message_id=str(echo["id"]),
                type=mtype, text=text, timestamp=_ts(echo.get("timestamp")),
                user_phone_e164=f"+{customer}" if _E164_DIGITS.match(customer) else None,
                is_echo=True, raw=echo,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            result.skipped += 1
            log.warning("whatsapp: skipped malformed echo: %s", exc)


# =========================================================================== sender
# أكواد أخطاء Cloud API الشائعة (راجع قائمة Meta الرسمية عند الحاجة)
_RETRYABLE_CODES = {
    4, 80007,      # rate limit على التطبيق / الحساب
    130429,        # throughput limit
    131056,        # pair rate limit (رسائل كثيرة لنفس الرقم)
    131000,        # Something went wrong (مؤقت عادة)
    133004,        # server temporarily unavailable
}
REENGAGEMENT_REQUIRED = 131047  # خارج نافذة الـ 24 ساعة => يلزم Template


class WhatsAppClient:
    """يُنشأ مرة واحدة ويُشارك httpx.AsyncClient (connection pooling)."""

    def __init__(self, http: httpx.AsyncClient, *, base_url: str, api_version: str) -> None:
        self._http = http
        self._base = f"{base_url.rstrip('/')}/{api_version}"

    async def send_text(self, *, account_external_id: str, access_token: str,
                        to: str, body: str) -> SendResult:
        body = body.strip()
        if not body:
            raise ChannelSendError("empty text body", retryable=False, code="empty_body")
        if len(body) > TEXT_MAX_CHARS:
            body = body[: TEXT_MAX_CHARS - 1] + "…"
        return await self._post(account_external_id, access_token, {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        })

    async def send_template(self, *, account_external_id: str, access_token: str, to: str,
                            name: str, language_code: str = "ar",
                            body_params: list[str] | None = None) -> SendResult:
        template: dict[str, Any] = {"name": name, "language": {"code": language_code}}
        if body_params:
            template["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            }]
        return await self._post(account_external_id, access_token, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": template,
        })

    async def _post(self, phone_number_id: str, token: str, payload: dict[str, Any]) -> SendResult:
        url = f"{self._base}/{phone_number_id}/messages"
        try:
            resp = await self._http.post(url, json=payload,
                                         headers={"Authorization": f"Bearer {token}"})
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            # ملاحظة: عند timeout قد تكون الرسالة أُرسلت فعلاً => احتمال تكرار نادر (at-least-once)
            raise ChannelSendError(f"network error: {type(exc).__name__}", retryable=True,
                                   code="network") from exc

        if resp.status_code == 200:
            try:
                return SendResult(external_message_id=resp.json()["messages"][0]["id"])
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise ChannelSendError("unexpected success payload", retryable=False,
                                       code="bad_response", http_status=200) from exc

        raise self._to_error(resp)

    @staticmethod
    def _to_error(resp: httpx.Response) -> ChannelSendError:
        code: int | str | None = None
        message = resp.text[:300]
        try:
            err = resp.json().get("error") or {}
            code = err.get("code")
            message = err.get("message") or message
            details = (err.get("error_data") or {}).get("details")
            if details:
                message = f"{message}: {details}"
        except ValueError:
            pass

        retryable = (
            resp.status_code == 429
            or resp.status_code >= 500
            or (isinstance(code, int) and code in _RETRYABLE_CODES)
        )
        if code == 190:  # توكن منتهي/ملغى — لا فائدة من إعادة المحاولة
            retryable = False
        return ChannelSendError(message, retryable=retryable, code=code,
                                http_status=resp.status_code)
