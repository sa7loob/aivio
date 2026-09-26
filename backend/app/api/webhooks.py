"""Meta webhooks: one endpoint for WhatsApp, Messenger and Instagram.

POST يفعل ثلاثة أشياء فقط: تحقق من التوقيع -> INSERT في webhook_events -> 200.
لا LLM ولا Graph API ولا parsing للرسائل هنا؛ كل ذلك في الـ worker.
"""
from __future__ import annotations

import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import SQLAlchemyError

from app.channels.meta_signature import verify_meta_signature
from app.core.config import get_settings
from app.db import queries as q
from app.db.tenant import system_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/meta", response_class=PlainTextResponse)
async def verify_subscription(
    mode: str | None = Query(None, alias="hub.mode"),
    verify_token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> str:
    expected = get_settings().meta_verify_token.get_secret_value()
    if (
        mode == "subscribe"
        and verify_token is not None
        and challenge is not None
        and hmac.compare_digest(verify_token, expected)
    ):
        return challenge  # يجب أن يُعاد كنص خام كما هو
    log.warning("webhook verification rejected (mode=%s)", mode)
    raise HTTPException(status.HTTP_403_FORBIDDEN, "verification failed")


@router.post("/meta")
async def receive_event(request: Request) -> dict[str, str]:
    settings = get_settings()

    # 1) raw body (نفس البايتات التي وقّعتها Meta)
    raw = await request.body()
    if len(raw) > settings.webhook_max_body_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "payload too large")

    # 2) التوقيع
    if not verify_meta_signature(
        raw, request.headers.get("X-Hub-Signature-256"),
        settings.meta_app_secret.get_secret_value(),
    ):
        log.warning("webhook rejected: invalid signature (ip=%s)",
                    request.client.host if request.client else "?")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")

    # 3) JSON صالح فقط (لا نحلل المحتوى هنا)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid json") from None
    object_type = payload.get("object") if isinstance(payload, dict) else None

    # 4) التخزين. إن فشل نعيد 5xx عمداً => Meta تعيد الإرسال لاحقاً ولا نفقد الرسالة.
    try:
        async with system_session() as session:
            await session.execute(q.INSERT_WEBHOOK_EVENT, {
                "provider": "meta",
                "object_type": object_type,
                "payload": raw.decode("utf-8"),
            })
    except SQLAlchemyError:
        log.exception("webhook: failed to persist event")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "temporarily unavailable") from None

    return {"status": "ok"}
