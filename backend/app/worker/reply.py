"""Stage 2: due conversations (after debounce) -> Agent -> reply queued in outbound_messages.

ثلاث مراحل حتى لا تبقى transaction أو قفل صف مفتوحاً أثناء استدعاء الـ LLM (ثوانٍ):

    tx1 (قفل قصير)  : تحقق من الحالة + لقطة: الرسائل المعلّقة، التاريخ، بيانات الوكالة والزبون
    Agent (بدون tx) : الـ LLM + الأدوات (كل أداة transaction قصيرة خاصة بها)
    tx2 (قفل قصير)  : حفظ الرد + outbound + تعليم الرسائل + agent_run + مسح موعد الرد

الـ lease على المحادثة (reply_lease_until) يمنع worker آخر من أخذها في الأثناء؛
لذلك مهلة الـ Agent أقل من مدة الـ lease.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.agent.core import FALLBACK_REPLY, Agent
from app.agent.history import build_history
from app.agent.types import (
    AgentInput,
    AgentOutput,
    CustomerProfile,
    TenantProfile,
    ToolContext,
)
from app.core.config import Settings
from app.db import queries as q
from app.db.tenant import system_session, tenant_session
from app.llm.base import EmbeddingClient

log = logging.getLogger(__name__)

WINDOW_24H = timedelta(hours=24)


@dataclass(frozen=True)
class ReplyDeps:
    settings: Settings
    agent: Agent
    embedder: EmbeddingClient | None


@dataclass(frozen=True)
class Snapshot:
    tenant: TenantProfile
    customer: CustomerProfile
    channel: str
    channel_account_id: UUID
    recipient: str
    pending_ids: list[UUID]
    history: list[Any]


async def run_reply_batch(deps: ReplyDeps) -> int:
    s_ = deps.settings
    async with system_session() as s:
        due = (await s.execute(q.CLAIM_DUE_CONVERSATIONS, {
            "batch": s_.worker_conversation_batch,
            "lease_seconds": float(s_.worker_conversation_lease_seconds),
        })).mappings().all()

    # محادثات مختلفة => بالتوازي (كل واحدة تنتظر الـ LLM)
    await asyncio.gather(*(_safe_reply(deps, row) for row in due))
    return len(due)


async def _safe_reply(deps: ReplyDeps, row: Any) -> None:
    try:
        await reply_to_conversation(deps, row["tenant_id"], row["conversation_id"], row["reply_due_at"])
    except Exception:  # noqa: BLE001 — الـ lease ينتهي وتُعاد المحاولة
        log.exception("reply: conversation %s failed", row["conversation_id"])


async def reply_to_conversation(deps: ReplyDeps, tenant_id: UUID, conversation_id: UUID,
                                claimed_due: datetime) -> None:
    settings = deps.settings

    # ------------------------------------------------------------ tx1: snapshot
    snap = await _take_snapshot(settings, tenant_id, conversation_id, claimed_due)
    if snap is None:
        return

    # ------------------------------------------------------------ Agent (no transaction)
    ctx = ToolContext(
        tenant_id=tenant_id, conversation_id=conversation_id, customer=snap.customer,
        session_factory=tenant_session, embedder=deps.embedder,
        lead_template_name=snap.tenant.settings.get("lead_template_name", settings.lead_template_name),
        lead_template_language=settings.lead_template_language,
        price_stale_days=settings.agent_price_stale_days,
        knowledge_top_k=settings.agent_knowledge_top_k,
        handoff_pause_hours=settings.agent_handoff_pause_hours,
    )
    inp = AgentInput(tenant=snap.tenant, customer=snap.customer, conversation_id=conversation_id,
                     history=build_history(snap.history), now=datetime.now(timezone.utc))
    deadline = settings.worker_conversation_lease_seconds * 0.75
    try:
        out = await asyncio.wait_for(deps.agent.run(inp, ctx), timeout=deadline)
    except asyncio.TimeoutError:
        log.error("reply: agent exceeded %.0fs for conversation %s", deadline, conversation_id)
        out = AgentOutput(reply_text=FALLBACK_REPLY, status="error", model=deps.agent.llm.model,
                          needs_human=True, error="agent_timeout",
                          lead_id=ctx.lead_id, handed_off=ctx.handed_off)

    # ------------------------------------------------------------ tx2: persist
    await _persist(settings, tenant_id, conversation_id, claimed_due, snap, out)


async def _take_snapshot(settings: Settings, tenant_id: UUID, conversation_id: UUID,
                         claimed_due: datetime) -> Snapshot | None:
    async with tenant_session(tenant_id) as s:
        conv = (await s.execute(q.LOCK_CONVERSATION,
                                {"conversation_id": conversation_id})).mappings().first()
        if conv is None:
            return None

        # وصلت رسالة بعد الحجز => الدورة القادمة تجمعها كلها في رد واحد
        if conv["reply_due_at"] != claimed_due:
            await s.execute(q.RELEASE_CONVERSATION_LEASE, {"conversation_id": conversation_id})
            return None

        now = datetime.now(timezone.utc)
        paused = conv["bot_paused_until"] is not None and conv["bot_paused_until"] > now
        clear = {"conversation_id": conversation_id, "claimed_due": claimed_due}
        if conv["mode"] != "bot" or paused:
            await s.execute(q.CLEAR_CONVERSATION_DUE, clear)
            return None

        pending = (await s.execute(q.PENDING_INBOUND_MESSAGES,
                                   {"conversation_id": conversation_id})).mappings().all()
        pending_ids = [p["id"] for p in pending]
        in_window = conv["last_inbound_at"] is not None and now - conv["last_inbound_at"] < WINDOW_24H
        if not pending or not in_window:
            if pending_ids:
                await s.execute(q.MARK_MESSAGES_HANDLED, {"ids": pending_ids})
            await s.execute(q.CLEAR_CONVERSATION_DUE, clear)
            return None

        t = (await s.execute(q.LOAD_TENANT_PROFILE)).mappings().one()
        history = (await s.execute(q.LOAD_HISTORY, {
            "conversation_id": conversation_id,
            "limit": settings.agent_history_messages})).mappings().all()

    settings_json = t["settings"] if isinstance(t["settings"], dict) else json.loads(t["settings"] or "{}")
    return Snapshot(
        tenant=TenantProfile(tenant_id=t["id"], name=t["name"], timezone=t["timezone"],
                             currency=t["default_currency"], settings=settings_json),
        customer=CustomerProfile(contact_id=conv["contact_id"], channel=conv["channel"],
                                 display_name=conv["contact_name"], phone_e164=conv["contact_phone"]),
        channel=conv["channel"],
        channel_account_id=conv["channel_account_id"],
        recipient=conv["recipient"],
        pending_ids=pending_ids,
        history=list(history),
    )


async def _persist(settings: Settings, tenant_id: UUID, conversation_id: UUID,
                   claimed_due: datetime, snap: Snapshot, out: AgentOutput) -> None:
    async with tenant_session(tenant_id) as s:
        # نفس ترتيب الأقفال مثل tx1 (المحادثة أولاً) لتجنب deadlock مع ingest
        conv = (await s.execute(q.LOCK_CONVERSATION, {"conversation_id": conversation_id})).mappings().first()
        if conv is None:
            return

        # استلم موظف المحادثة (من اللوحة أو رد من تطبيق الهاتف) أثناء تشغيل الـ Agent => الرد يُهمل.
        # إيقاف البوت بسبب handoff_to_human في نفس التشغيل ليس استلاماً => رد التحويل يُرسل.
        if should_discard(conv["mode"], conv["bot_paused_until"], out.handed_off):
            await _record_discarded(s, conversation_id, claimed_due, out)
            log.info("reply: conversation=%s discarded (mode=%s, human took over during agent run)",
                     conversation_id, conv["mode"])
            return

        reply_message_id = None
        if out.reply_text:
            reply_message_id = (await s.execute(q.INSERT_OUTBOUND_MESSAGE_RECORD, {
                "conversation_id": conversation_id, "channel": snap.channel,
                "text_content": out.reply_text,
            })).scalar_one()
            await s.execute(q.ENQUEUE_OUTBOUND, {
                "channel_account_id": snap.channel_account_id,
                "recipient": snap.recipient,
                "purpose": "reply",
                "conversation_id": conversation_id,
                "message_id": reply_message_id,
                "kind": "text",
                "body": json.dumps({"text": out.reply_text}, ensure_ascii=False),
            })

        # فقط الرسائل التي رآها الـ Agent؛ أي رسالة وصلت أثناء التشغيل تبقى للدورة القادمة
        await s.execute(q.MARK_MESSAGES_HANDLED, {"ids": snap.pending_ids})

        if out.needs_human and not out.handed_off:
            await s.execute(q.PAUSE_BOT, {"conversation_id": conversation_id,
                                          "hours": settings.agent_handoff_pause_hours})

        await s.execute(q.INSERT_AGENT_RUN, {
            "conversation_id": conversation_id, "reply_message_id": reply_message_id,
            "status": out.status, "model": out.model, "iterations": out.iterations,
            "input_tokens": out.input_tokens, "output_tokens": out.output_tokens,
            "latency_ms": out.latency_ms,
            "tool_calls": json.dumps([t.as_dict() for t in out.traces], ensure_ascii=False,
                                     default=str),
            "handed_off": out.handed_off or out.needs_human,
            "lead_id": out.lead_id, "error": out.error,
        })
        await s.execute(q.CLEAR_CONVERSATION_DUE,
                        {"conversation_id": conversation_id, "claimed_due": claimed_due})

    log.info("reply: conversation=%s status=%s tools=%s tokens=%d/%d %dms lead=%s",
             conversation_id, out.status, [t.name for t in out.traces],
             out.input_tokens, out.output_tokens, out.latency_ms, out.lead_id)


def should_discard(mode: str, bot_paused_until: datetime | None, handed_off: bool,
                   now: datetime | None = None) -> bool:
    if mode != "bot":
        return True
    paused = bot_paused_until is not None and bot_paused_until > (now or datetime.now(timezone.utc))
    return paused and not handed_off


async def _record_discarded(s: Any, conversation_id: UUID, claimed_due: datetime, out: AgentOutput) -> None:
    """لا رسالة ولا outbound. رسائل الزبون تبقى بدون handled_at: رد الموظف يعلّمها،
    أو إن أُرجعت المحادثة للبوت يرد عليها (RELEASE)."""
    await s.execute(q.INSERT_AGENT_RUN, {
        "conversation_id": conversation_id, "reply_message_id": None,
        "status": "discarded", "model": out.model, "iterations": out.iterations,
        "input_tokens": out.input_tokens, "output_tokens": out.output_tokens,
        "latency_ms": out.latency_ms,
        "tool_calls": json.dumps([t.as_dict() for t in out.traces], ensure_ascii=False, default=str),
        "handed_off": out.handed_off, "lead_id": out.lead_id,
        "error": "discarded: human took over during agent run",
    })
    await s.execute(q.CLEAR_CONVERSATION_DUE, {"conversation_id": conversation_id, "claimed_due": claimed_due})
