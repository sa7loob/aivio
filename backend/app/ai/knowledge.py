"""Staff answers -> knowledge for the bot (المرحلة 7a).

الموظف رد على سؤال لم يعرفه البوت => «احفظ ردك كمعلومة للبوت»:
- الاقتراح: آخر كتلة رسائل من الزبون قبل رد الموظف = السؤال، ورد الموظف (ورسائله المتتالية) = الجواب.
  الموظف يعدّلهما قبل الحفظ.
- الحفظ: knowledge_chunk (faq) متاحة فوراً للبحث النصي؛ الـ embedding في الـ worker (embed_knowledge)
  فلا يفشل الحفظ إن تعطّل OpenAI، ولا يستدعي الـ API مزوداً خارجياً.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.ai import queries as aq
from app.ai.jobs import AIDeps, Job, JobResult, PermanentJobError
from app.agent.knowledge_ingest import MAX_CHUNK_CHARS
from app.db.tenant import tenant_session

log = logging.getLogger(__name__)

QUESTION_MAX_CHARS = 500
ANSWER_MAX_CHARS = MAX_CHUNK_CHARS


@dataclass(frozen=True)
class Suggestion:
    question: str
    answer: str
    message_id: UUID          # رد الموظف الذي بُني عليه الاقتراح


def _text(m: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", " ", m.get("text_content") or "").strip()


def suggest_knowledge(messages_oldest_first: Iterable[Mapping[str, Any]],
                      staff_message_id: UUID | None = None) -> Suggestion | None:
    """messages: رسائل المحادثة (الأقدم أولاً). staff_message_id: رد موظف محدد، وإلا آخر رد موظف.
    السؤال = رسائل الزبون المتتالية الأقرب قبل الرد (تتخطى رسائل البوت بينهما، مثل رسالة التحويل)."""
    msgs = list(messages_oldest_first)
    staff_idx = [i for i, m in enumerate(msgs)
                 if m["direction"] == "outbound" and m["sender_type"] == "staff" and _text(m)]
    if staff_message_id is not None:
        staff_idx = [i for i in staff_idx if msgs[i]["id"] == staff_message_id]
    if not staff_idx:
        return None
    # الجواب = كتلة ردود الموظف المتتالية التي فيها الرد المختار (قد يجيب في أكثر من رسالة).
    # معرّف الكتلة = أول رسالة فيها => اختيار أي رسالة من نفس الكتلة يعطي نفس المعلومة (بدون تكرار)
    target = staff_idx[-1]
    while target > 0 and msgs[target - 1]["direction"] == "outbound" and msgs[target - 1]["sender_type"] == "staff":
        target -= 1
    answer_parts: list[str] = []
    for m in msgs[target:]:
        if m["direction"] == "inbound":
            break
        if m["sender_type"] == "staff" and _text(m):
            answer_parts.append(_text(m))

    # السؤال: نرجع للخلف متخطين الرسائل الصادرة حتى أول رسالة زبون، ثم نجمع كتلتها المتتالية
    i = target - 1
    while i >= 0 and msgs[i]["direction"] != "inbound":
        i -= 1
    question_parts: list[str] = []
    while i >= 0 and msgs[i]["direction"] == "inbound":
        if _text(msgs[i]):
            question_parts.insert(0, _text(msgs[i]))
        i -= 1
    if not question_parts:
        return None
    return Suggestion(question=" ".join(question_parts)[:QUESTION_MAX_CHARS],
                      answer="\n".join(answer_parts)[:ANSWER_MAX_CHARS],
                      message_id=msgs[target]["id"])


def knowledge_source_ref(conversation_id: UUID, message_id: UUID | None) -> str:
    """نفس الرد لا يُحفظ مرتين (idempotency)."""
    base = f"conversation:{conversation_id}"
    return f"{base}:message:{message_id}" if message_id else base


def chunk_title(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip()[:200]


# ------------------------------------------------------------------ embedding job
def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


class EmbedKnowledgeHandler:
    kind = "embed_knowledge"
    max_attempts = 6
    retry_base_seconds = 10.0

    async def run(self, deps: AIDeps, job: Job) -> JobResult:
        async with tenant_session(job.tenant_id) as s:
            k = (await s.execute(aq.KNOWLEDGE_FOR_EMBEDDING, {"id": job.knowledge_chunk_id})).mappings().first()
        if k is None or not k["is_active"]:
            return JobResult(note="chunk deleted or inactive")
        if deps.embedder is None:
            raise PermanentJobError("embeddings not configured")
        # نفس صيغة knowledge_ingest: العنوان يحسّن التطابق الدلالي
        vector = (await deps.embedder.embed([f"{k['title'] or ''}\n{k['content']}"]))[0]
        async with tenant_session(job.tenant_id) as s:
            await s.execute(aq.SET_KNOWLEDGE_EMBEDDING, {"id": job.knowledge_chunk_id,
                                                         "embedding": vector_literal(vector),
                                                         "model": deps.embedder.model})
        return JobResult(model=deps.embedder.model)

    async def on_final_failure(self, deps: AIDeps, job: Job, error: str) -> None:
        # المعلومة تبقى متاحة للبحث النصي؛ البحث الدلالي فقط لا يشملها
        return None
