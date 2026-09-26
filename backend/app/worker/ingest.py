"""Stage 1: webhook_events -> contacts / conversations / messages (+ debounce)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.ai import queries as aq
from app.ai.transcription import audio_source
from app.channels.base import InboundMessage
from app.channels.messenger import is_own_echo
from app.channels.registry import parser_for
from app.core.config import Settings
from app.db import queries as q
from app.db.tenant import system_session, tenant_session
from app.worker.retry import backoff_seconds

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedAccount:
    channel_account_id: UUID
    tenant_id: UUID


class UnroutableMessage(Exception):
    """الحساب غير مسجّل أو معطّل: ليس خطأً مؤقتاً، لا نعيد المحاولة."""


async def run_ingest_batch(settings: Settings) -> int:
    async with system_session() as s:
        events = (await s.execute(q.CLAIM_WEBHOOK_EVENTS, {
            "batch": settings.worker_event_batch,
            "lease_seconds": float(settings.worker_event_lease_seconds),
        })).mappings().all()
    # الحجز (lease) أصبح committed: أي worker آخر لن يأخذ هذه الأحداث

    for ev in events:
        await _process_one_event(settings, ev)
    return len(events)


async def _process_one_event(settings: Settings, ev: Any) -> None:
    event_id, attempts = ev["id"], ev["attempts"]
    try:
        final_status, note = await _handle_event(settings, ev["object_type"], ev["payload"])
        async with system_session() as s:
            await s.execute(q.FINISH_WEBHOOK_EVENT,
                            {"id": event_id, "status": final_status, "error": note})
    except Exception as exc:  # noqa: BLE001 — أي خطأ غير متوقع => إعادة محاولة ثم failed
        log.exception("ingest: event %s failed (attempt %s)", event_id, attempts)
        error = f"{type(exc).__name__}: {exc}"[:1000]
        async with system_session() as s:
            if attempts >= settings.worker_max_attempts:
                await s.execute(q.FINISH_WEBHOOK_EVENT,
                                {"id": event_id, "status": "failed", "error": error})
            else:
                await s.execute(q.RETRY_WEBHOOK_EVENT, {
                    "id": event_id, "error": error,
                    "delay_seconds": backoff_seconds(attempts),
                })


async def _handle_event(settings: Settings, object_type: str | None,
                        payload: Any) -> tuple[str, str | None]:
    parser = parser_for(object_type)
    if parser is None:
        return "ignored", f"unsupported object type: {object_type}"

    if isinstance(payload, str):  # حسب إعداد codec الـ jsonb في asyncpg
        payload = json.loads(payload)
    parsed = parser(payload)

    unroutable: list[str] = []
    cache: dict[tuple[str, str], ResolvedAccount] = {}
    for msg in parsed.messages:
        try:
            account = await _resolve(cache, msg.channel, msg.account_external_id)
        except UnroutableMessage as exc:
            unroutable.append(str(exc))
            continue
        if msg.is_echo:
            await _store_echo(settings, account, msg)
        else:
            await _store_inbound(settings, account, msg)

    for st in parsed.statuses:
        if st.status == "failed":
            # فشل تسليم رسالة أرسلناها (مثلاً خارج نافذة 24 ساعة) — للمراقبة
            log.warning("delivery failed: %s %s", st.external_message_id, st.errors)

    notes = []
    if parsed.skipped:
        notes.append(f"skipped={parsed.skipped}")
    if unroutable:
        notes.append("unroutable=" + ",".join(sorted(set(unroutable))))
    if not parsed.messages and not parsed.statuses:
        return "ignored", "; ".join(notes) or "no messages"
    return "done", "; ".join(notes) or None


async def _resolve(cache: dict[tuple[str, str], ResolvedAccount],
                   channel: str, external_id: str) -> ResolvedAccount:
    key = (channel, external_id)
    if key in cache:
        return cache[key]
    async with system_session() as s:
        row = (await s.execute(q.RESOLVE_CHANNEL_ACCOUNT,
                               {"channel": channel, "external_id": external_id})).mappings().first()
    if row is None:
        log.warning("ingest: no tenant for %s account %s", channel, external_id)
        raise UnroutableMessage(f"{channel}:{external_id}")
    if row["status"] != "active":
        raise UnroutableMessage(f"{channel}:{external_id}:{row['status']}")
    cache[key] = ResolvedAccount(row["channel_account_id"], row["tenant_id"])
    return cache[key]


async def _store_inbound(settings: Settings, account: ResolvedAccount,
                         msg: InboundMessage) -> None:
    """Transaction واحدة لكل رسالة، داخل سياق الوكالة. آمنة لإعادة التنفيذ (idempotent)."""
    async with tenant_session(account.tenant_id) as s:
        contact_id = (await s.execute(q.UPSERT_CONTACT, {
            "channel": msg.channel,
            "external_user_id": msg.user_external_id,
            "display_name": msg.user_display_name,
            "phone_e164": msg.user_phone_e164,
        })).scalar_one()

        conv = (await s.execute(q.UPSERT_OPEN_CONVERSATION, {
            "contact_id": contact_id,
            "channel_account_id": account.channel_account_id,
        })).mappings().one()

        inserted = (await s.execute(q.INSERT_INBOUND_MESSAGE, {
            "conversation_id": conv["id"],
            "channel": msg.channel,
            "external_message_id": msg.external_message_id,
            "msg_type": msg.type,
            "text_content": msg.text,
            "payload": json.dumps(msg.raw, ensure_ascii=False),
            "platform_ts": msg.timestamp,
        })).scalar_one_or_none()

        if inserted is None:
            log.info("ingest: duplicate %s ignored", msg.external_message_id)
            return

        await s.execute(q.BUMP_CONVERSATION_ON_INBOUND, {
            "conversation_id": conv["id"],
            "platform_ts": msg.timestamp,
            "debounce_seconds": settings.reply_debounce_seconds,
        })
        # رسالة صوتية => مهمة تفريغ في نفس الـ transaction (الرد ينتظرها حتى حد أقصى)
        if (msg.type == "audio" and settings.voice_transcription_enabled
                and audio_source(msg.channel, msg.raw) is not None):
            await s.execute(aq.ENQUEUE_TRANSCRIPTION, {"message_id": inserted})
        log.info("ingest: tenant=%s conversation=%s message=%s type=%s",
                 account.tenant_id, conv["id"], inserted, msg.type)


async def _store_echo(settings: Settings, account: ResolvedAccount, msg: InboundMessage) -> None:
    """رسالة خرجت من حساب الوكالة لكن ليس من خلالنا (Inbox الصفحة / تطبيق واتساب على الهاتف)
    => موظف تدخّل: نسجّلها كرسالة staff ونوقف البوت في المحادثة (Human takeover تلقائي).
    echo لرسالة أرسلها البوت نفسه يُتجاهل."""
    if is_own_echo(msg, settings.meta_app_id):
        return
    async with tenant_session(account.tenant_id) as s:
        if (await s.execute(q.MESSAGE_EXISTS, {"channel": msg.channel,
                                               "external_message_id": msg.external_message_id})).first():
            return
        if (await s.execute(q.RECENT_BOT_SEND_TO, {
                "channel_account_id": account.channel_account_id, "recipient": msg.user_external_id,
                "external_message_id": msg.external_message_id})).first():
            return   # ردنا نفسه وصل كـ echo قبل تسجيل معرّفه

        contact_id = (await s.execute(q.UPSERT_CONTACT, {
            "channel": msg.channel, "external_user_id": msg.user_external_id,
            "display_name": None, "phone_e164": msg.user_phone_e164,
        })).scalar_one()
        conv = (await s.execute(q.UPSERT_OPEN_CONVERSATION, {
            "contact_id": contact_id, "channel_account_id": account.channel_account_id,
        })).mappings().one()
        inserted = (await s.execute(q.INSERT_STAFF_ECHO_MESSAGE, {
            "conversation_id": conv["id"], "channel": msg.channel,
            "external_message_id": msg.external_message_id, "msg_type": msg.type,
            "text_content": msg.text, "payload": json.dumps(msg.raw, ensure_ascii=False),
            "platform_ts": msg.timestamp,
        })).scalar_one_or_none()
        if inserted is None:
            return
        await s.execute(q.HUMAN_TAKEOVER_BY_ECHO, {"conversation_id": conv["id"],
                                                   "hours": settings.agent_handoff_pause_hours})
        log.info("ingest: human takeover via echo tenant=%s conversation=%s channel=%s",
                 account.tenant_id, conv["id"], msg.channel)
