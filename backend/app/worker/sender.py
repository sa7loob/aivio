"""Stage 3: outbound_messages -> channel API (WhatsApp Cloud API in this slice).

لا نُبقي transaction مفتوحة أثناء طلب HTTP:
    tx1: قراءة الرسالة  ->  HTTP إلى Meta  ->  tx2: تسجيل النتيجة
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.channels.base import ChannelSendError, SendResult
from app.channels.registry import SenderRegistry
from app.core.config import Settings
from app.core.crypto import TokenDecryptionError, decrypt_token
from app.db import queries as q
from app.db.tenant import system_session, tenant_session
from app.worker.retry import backoff_seconds

log = logging.getLogger(__name__)

WINDOW_24H = timedelta(hours=24)


class PermanentSendFailure(Exception):
    pass


async def run_send_batch(settings: Settings, senders: SenderRegistry) -> int:
    async with system_session() as s:
        claimed = (await s.execute(q.CLAIM_PENDING_OUTBOUND, {
            "batch": settings.worker_outbound_batch,
            "lease_seconds": float(settings.worker_outbound_lease_seconds),
        })).mappings().all()

    for row in claimed:
        try:
            await _send_one(settings, senders, row["tenant_id"], row["outbound_id"])
        except Exception:  # noqa: BLE001 — الـ lease ينتهي وتُعاد المحاولة
            log.exception("send: outbound %s crashed", row["outbound_id"])
    return len(claimed)


async def _send_one(settings: Settings, senders: SenderRegistry,
                    tenant_id: UUID, outbound_id: UUID) -> None:
    # ---- tx1: قراءة
    async with tenant_session(tenant_id) as s:
        o = (await s.execute(q.LOAD_OUTBOUND, {"outbound_id": outbound_id})).mappings().first()
    if o is None:
        return  # أُلغيت أو أُرسلت من worker آخر

    # ---- HTTP (خارج أي transaction)
    try:
        result = await _dispatch(senders, o)
    except PermanentSendFailure as exc:
        await _finish(tenant_id, q.MARK_OUTBOUND_FAILED,
                      {"outbound_id": outbound_id, "status": "failed", "error": str(exc)})
        log.warning("send: outbound %s failed (permanent): %s", outbound_id, exc)
        return
    except ChannelSendError as exc:
        if exc.retryable and o["attempts"] < settings.worker_max_attempts:
            await _finish(tenant_id, q.MARK_OUTBOUND_RETRY, {
                "outbound_id": outbound_id, "error": str(exc)[:1000],
                "delay_seconds": backoff_seconds(o["attempts"]),
            })
            log.warning("send: outbound %s retry #%s: %s", outbound_id, o["attempts"], exc)
        else:
            await _finish(tenant_id, q.MARK_OUTBOUND_FAILED,
                          {"outbound_id": outbound_id, "status": "failed", "error": str(exc)[:1000]})
            log.error("send: outbound %s failed permanently: %s", outbound_id, exc)
        if exc.code == 190:
            await _finish(tenant_id, q.MARK_CHANNEL_NEEDS_REAUTH, {
                "channel_account_id": o["channel_account_id"], "error": str(exc)[:500]})
            log.error("send: channel %s token invalid -> needs_reauth", o["channel_account_id"])
        return

    # ---- tx2: نجاح
    await _finish(tenant_id, q.MARK_OUTBOUND_SENT, {
        "outbound_id": outbound_id, "external_message_id": result.external_message_id,
    })
    log.info("send: outbound %s sent as %s", outbound_id, result.external_message_id)


async def _dispatch(senders: SenderRegistry, o: Any) -> SendResult:
    if o["account_status"] != "active":
        raise PermanentSendFailure(f"channel account is {o['account_status']}")

    sender = senders.get(o["channel"])
    if sender is None:
        raise PermanentSendFailure(f"no sender for channel {o['channel']}")

    body = o["body"] if isinstance(o["body"], dict) else json.loads(o["body"])

    # رسالة نصية حرة للزبون مسموحة فقط داخل نافذة الـ 24 ساعة
    if o["kind"] == "text" and o["purpose"] != "staff_notification":
        last_in = o["last_inbound_at"]
        if last_in is None or datetime.now(timezone.utc) - last_in >= WINDOW_24H:
            raise PermanentSendFailure("outside 24h customer service window (template required)")

    try:
        token = decrypt_token(o["access_token_enc"])
    except TokenDecryptionError as exc:
        raise PermanentSendFailure(str(exc)) from exc

    # إنستغرام المربوط بصفحة يُرسل عبر /{page_id}/messages
    config = o["account_config"] if isinstance(o["account_config"], dict) else json.loads(o["account_config"] or "{}")
    send_from = config.get("page_id") if o["channel"] == "instagram" else None
    send_from = send_from or o["account_external_id"]

    if o["kind"] == "text":
        return await sender.send_text(account_external_id=send_from,
                                      access_token=token, to=o["recipient"], body=body["text"])
    if o["kind"] == "template":
        return await sender.send_template(
            account_external_id=o["account_external_id"], access_token=token, to=o["recipient"],
            name=body["name"], language_code=body.get("language", "ar"),
            body_params=body.get("params"),
        )
    raise PermanentSendFailure(f"unsupported kind {o['kind']}")


async def _finish(tenant_id: UUID, stmt: Any, params: dict[str, Any]) -> None:
    async with tenant_session(tenant_id) as s:
        await s.execute(stmt, params)
