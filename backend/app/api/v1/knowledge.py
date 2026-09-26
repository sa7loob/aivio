"""«احفظ ردك كمعلومة للبوت» + قائمة المعلومات (المرحلة 7a).

- الاقتراح يُبنى من المحادثة: رسائل الزبون قبل رد الموظف = السؤال، ورد الموظف = الجواب.
- الحفظ متاح لأي موظف (هو من أجاب)، ويُسجَّل من علّم البوت (created_by). التعطيل للمدير.
- الـ embedding يُحسب في الـ worker؛ المعلومة متاحة للبحث النصي فوراً.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.ai import queries as aq
from app.ai.knowledge import ANSWER_MAX_CHARS, QUESTION_MAX_CHARS, chunk_title, knowledge_source_ref, suggest_knowledge
from app.api.deps import ADMIN, MEMBER, TenantContext
from app.db.tenant import tenant_session

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


class KnowledgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(..., min_length=3, max_length=QUESTION_MAX_CHARS)
    answer: str = Field(..., min_length=2, max_length=ANSWER_MAX_CHARS)
    message_id: UUID | None = Field(None, description="رد الموظف الذي بُنيت عليه المعلومة (يمنع الحفظ مرتين)")


def _conversation_not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, {"error": "conversation_not_found"})


@router.get("/conversations/{conversation_id}/knowledge-suggestion")
async def knowledge_suggestion(conversation_id: UUID, message_id: UUID | None = None,
                               ctx: TenantContext = Depends(MEMBER)) -> dict:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        if (await s.execute(aq.CONVERSATION_EXISTS, {"id": conversation_id})).first() is None:
            raise _conversation_not_found()
        rows = (await s.execute(aq.CONVERSATION_RECENT_MESSAGES,
                                {"conversation_id": conversation_id})).mappings().all()
    suggestion = suggest_knowledge(reversed(rows), message_id)
    if suggestion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "error": "no_staff_answer", "message": "لا يوجد رد موظف على سؤال زبون في هذه المحادثة"})
    return {"question": suggestion.question, "answer": suggestion.answer, "message_id": suggestion.message_id}


@router.post("/conversations/{conversation_id}/knowledge", status_code=status.HTTP_201_CREATED)
async def save_knowledge(conversation_id: UUID, body: KnowledgeIn, response: Response,
                         ctx: TenantContext = Depends(MEMBER)) -> dict:
    question, answer = body.question.strip(), body.answer.strip()
    if len(question) < 3 or len(answer) < 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "empty_knowledge"})
    source_ref = knowledge_source_ref(conversation_id, body.message_id)
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        if (await s.execute(aq.CONVERSATION_EXISTS, {"id": conversation_id})).first() is None:
            raise _conversation_not_found()
        if body.message_id is not None:
            existing = (await s.execute(aq.KNOWLEDGE_BY_SOURCE_REF, {"source_ref": source_ref})).mappings().first()
            if existing is not None:
                response.status_code = status.HTTP_200_OK
                return {"id": existing["id"], "duplicate": True}
        row = (await s.execute(aq.INSERT_STAFF_KNOWLEDGE, {
            "source_ref": source_ref, "title": chunk_title(question), "content": answer})).mappings().one()
        await s.execute(aq.ENQUEUE_EMBEDDING, {"knowledge_chunk_id": row["id"]})
    return {"id": row["id"], "duplicate": False, "embedding": "pending"}


@router.get("/knowledge")
async def list_knowledge(source: Literal["all", "staff"] = "all", include_inactive: bool = False,
                         ctx: TenantContext = Depends(MEMBER)) -> list[dict]:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        return [dict(r) for r in (await s.execute(aq.LIST_KNOWLEDGE, {
            "only_staff": source == "staff", "include_inactive": include_inactive})).mappings().all()]


@router.delete("/knowledge/{chunk_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_knowledge(chunk_id: UUID, ctx: TenantContext = Depends(ADMIN)) -> Response:
    """البوت يتوقف عن استخدام المعلومة فوراً (تبقى في السجل)."""
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        if (await s.execute(aq.DEACTIVATE_KNOWLEDGE, {"id": chunk_id})).first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "knowledge_not_found"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
