"""Messenger + Instagram (Messenger Platform, Page-linked IG) adapter: webhook parsers + sender.

Webhook shape (object = "page" | "instagram"):
    entry[].id                 -> page_id / ig_account_id (مفتاح التوجيه للوكالة)
    entry[].messaging[]:
        sender.id / recipient.id / timestamp (ms)
        message {mid, text, attachments[], quick_reply, is_echo, app_id, metadata, is_deleted, ...}
        postback {mid, title, payload}
        read / delivery / reaction / messaging_seen  -> تُتجاهل

الإرسال (الاثنان): POST /{page_id}/messages  {recipient.id, messaging_type: RESPONSE, message.text}
  - إنستغرام المربوط بصفحة يستخدم نفس الـ endpoint مع IGSID وتوكن الصفحة.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.channels.base import (
    Channel,
    ChannelSendError,
    InboundMessage,
    MessageType,
    ParsedWebhook,
    SendResult,
)

log = logging.getLogger(__name__)

OBJECT_PAGE = "page"
OBJECT_INSTAGRAM = "instagram"
# يُرفق مع كل رسالة يرسلها البوت على ماسنجر ويعود في الـ echo => نميّز رد البوت عن رد الموظف
BOT_ECHO_METADATA = "ai-agent"

_ATTACHMENT_TYPES: dict[str, MessageType] = {
    "image": "image", "video": "video", "audio": "audio", "file": "document",
    "location": "location", "ig_reel": "video", "reel": "video",
}
_TEXT_LIMITS = {"messenger": 2000, "instagram": 1000}


def _ts_ms(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc)


def _message_content(m: dict[str, Any]) -> tuple[MessageType, str | None]:
    if m.get("quick_reply"):
        return "interactive", m.get("text")
    if m.get("text"):
        return "text", m["text"]
    attachments = m.get("attachments") or []
    if attachments:
        a = attachments[0] or {}
        atype = a.get("type", "")
        title = (a.get("payload") or {}).get("title")
        if atype in _ATTACHMENT_TYPES:
            return _ATTACHMENT_TYPES[atype], title
        return "other", title or atype or None       # share / story_mention / fallback / template
    if m.get("sticker_id"):
        return "image", None
    return "other", None


def _parse(payload: dict[str, Any], expected_object: str, channel: Channel) -> ParsedWebhook:
    result = ParsedWebhook()
    if payload.get("object") != expected_object:
        return result

    for entry in payload.get("entry") or []:
        account_id = str((entry or {}).get("id") or "")
        if not account_id:
            result.skipped += 1
            continue
        for ev in entry.get("messaging") or []:     # "standby" (handover) يُتجاهل عمداً
            try:
                sender = str(ev["sender"]["id"])
                recipient = str(ev["recipient"]["id"])
                ts = _ts_ms(ev.get("timestamp"))
                if "message" in ev:
                    m = ev["message"] or {}
                    if m.get("is_deleted") or m.get("is_unsupported"):
                        result.skipped += 1
                        continue
                    is_echo = bool(m.get("is_echo"))
                    mtype, text = _message_content(m)
                    result.messages.append(InboundMessage(
                        channel=channel, account_external_id=account_id,
                        # في الـ echo: المرسل هو الصفحة والمستلم هو الزبون
                        user_external_id=recipient if is_echo else sender,
                        external_message_id=str(m["mid"]), type=mtype, text=text,
                        timestamp=ts, is_echo=is_echo, raw=ev,
                    ))
                elif "postback" in ev:
                    pb = ev["postback"] or {}
                    result.messages.append(InboundMessage(
                        channel=channel, account_external_id=account_id, user_external_id=sender,
                        external_message_id=str(pb.get("mid") or f"postback:{sender}:{ev.get('timestamp')}"),
                        type="interactive", text=pb.get("title") or pb.get("payload"),
                        timestamp=ts, raw=ev,
                    ))
                # read / delivery / reaction / messaging_seen: لا شيء للـ Agent
            except (KeyError, TypeError, ValueError) as exc:
                result.skipped += 1
                log.warning("%s: skipped malformed event: %s", channel, exc)
    return result


def parse_messenger(payload: dict[str, Any]) -> ParsedWebhook:
    return _parse(payload, OBJECT_PAGE, "messenger")


def parse_instagram(payload: dict[str, Any]) -> ParsedWebhook:
    return _parse(payload, OBJECT_INSTAGRAM, "instagram")


def is_own_echo(msg: InboundMessage, meta_app_id: str | None) -> bool:
    """echo لرسالة أرسلها البوت نفسه (وليس موظفاً من Inbox الصفحة)."""
    m = (msg.raw or {}).get("message") or {}
    if m.get("metadata") == BOT_ECHO_METADATA:
        return True
    return bool(meta_app_id) and str(m.get("app_id") or "") == str(meta_app_id)


# =========================================================================== sender
_RETRYABLE = {1, 2, 4, 17, 32, 613}      # أخطاء مؤقتة / rate limits
# 10/2018278: خارج نافذة 24 ساعة، 551: المستخدم غير متاح، 190: توكن، 200: صلاحية => دائمة


class MessengerClient:
    """نسخة واحدة لكل قناة (messenger / instagram) لاختلاف حد طول النص فقط."""

    def __init__(self, http: httpx.AsyncClient, *, base_url: str, api_version: str,
                 channel: Channel = "messenger") -> None:
        self._http = http
        self._base = f"{base_url.rstrip('/')}/{api_version}"
        self._channel = channel
        self._limit = _TEXT_LIMITS.get(channel, 1000)

    async def send_text(self, *, account_external_id: str, access_token: str,
                        to: str, body: str) -> SendResult:
        body = body.strip()
        if not body:
            raise ChannelSendError("empty text body", retryable=False, code="empty_body")
        if len(body) > self._limit:
            body = body[: self._limit - 1] + "…"
        message: dict[str, Any] = {"text": body}
        if self._channel == "messenger":
            message["metadata"] = BOT_ECHO_METADATA
        payload = {"recipient": {"id": to}, "messaging_type": "RESPONSE", "message": message}
        try:
            resp = await self._http.post(f"{self._base}/{account_external_id}/messages", json=payload,
                                         headers={"Authorization": f"Bearer {access_token}"})
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ChannelSendError(f"network error: {type(exc).__name__}", retryable=True,
                                   code="network") from exc
        if resp.status_code == 200:
            try:
                return SendResult(external_message_id=resp.json()["message_id"])
            except (ValueError, KeyError, TypeError) as exc:
                raise ChannelSendError("unexpected success payload", retryable=False,
                                       code="bad_response", http_status=200) from exc
        code: int | str | None = None
        message_text = resp.text[:300]
        try:
            err = resp.json().get("error") or {}
            code, message_text = err.get("code"), err.get("message") or message_text
        except ValueError:
            pass
        retryable = resp.status_code >= 500 or resp.status_code == 429 or code in _RETRYABLE
        raise ChannelSendError(message_text, retryable=retryable, code=code, http_status=resp.status_code)

    async def send_template(self, **_: Any) -> SendResult:
        raise ChannelSendError(f"templates are not supported on {self._channel}", retryable=False,
                               code="unsupported")
