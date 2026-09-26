"""Live Inbox: conversations, messages, human takeover, staff replies.

قاعدة التزامن بين الموظف والبوت:
  - الاستلام (takeover) أو أول رد من الموظف => mode='human' تحت قفل صف المحادثة، وتُلغى ردود البوت المعلّقة.
  - البوت يتحقق من mode تحت نفس القفل قبل حفظ رده (reply.py) => أي رد انتهى بعد الاستلام يُهمل.
  => لا يرسل البوت والموظف في نفس المحادثة في نفس الوقت.
"""
from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import MEMBER, TenantContext
from app.dashboard import queries as dq
from app.dashboard.logic import decode_cursor, next_cursor, window_open
from app.db import queries as q
from app.db.tenant import tenant_session

router = APIRouter(prefix="/api/v1", tags=["inbox"])

View = Literal["open", "human", "bot", "unread", "closed", "all"]
MAX_TEXT = 4000


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SendMessage(Strict):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT)
    client_msg_id: UUID = Field(..., description="UUID من الواجهة: إعادة الإرسال لا تكرر الرسالة")


class AssignIn(Strict):
    user_id: UUID | None = None


def _cursor(cursor: str | None):
    try:
        return decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"error": "invalid_cursor"}) from None


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, {"error": "conversation_not_found"})


# ------------------------------------------------------------------ read
@router.get("/conversations")
async def list_conversations(view: View = "open", channel: str | None = None,
                             assigned: Literal["me", "unassigned", "any"] = "any",
                             q_: str | None = Query(None, alias="q", max_length=100),
                             cursor: str | None = None, limit: int = Query(30, ge=1, le=100),
                             ctx: TenantContext = Depends(MEMBER)) -> dict:
    ts, cid = _cursor(cursor)
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        rows = (await s.execute(dq.INBOX_LIST, {
            "view": view, "channel": channel, "assigned": assigned, "q": (q_ or "").strip() or None,
            "cursor_ts": ts, "cursor_id": cid, "limit": limit})).mappings().all()
    items = [dict(r) for r in rows]
    return {"items": items, "next_cursor": next_cursor(items, limit, "last_message_at")}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: UUID, ctx: TenantContext = Depends(MEMBER)) -> dict:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        row = (await s.execute(dq.CONVERSATION_DETAIL, {"id": conversation_id})).mappings().first()
    if row is None:
        raise _not_found()
    return {**dict(row), "reply_window_open": window_open(row["last_inbound_at"])}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: UUID, cursor: str | None = None,
                        limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(MEMBER)) -> dict:
    """الأحدث أولاً في الاستعلام، وتُعاد مرتبة زمنياً (الأقدم أولاً) للعرض. cursor => صفحة أقدم."""
    ts, cid = _cursor(cursor)
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        if (await s.execute(dq.CONVERSATION_DETAIL, {"id": conversation_id})).first() is None:
            raise _not_found()
        rows = [dict(r) for r in (await s.execute(dq.MESSAGES_PAGE, {
            "conversation_id": conversation_id, "cursor_ts": ts, "cursor_id": cid,
            "limit": limit})).mappings().all()]
    older = next_cursor(rows, limit, "created_at")
    return {"items": list(reversed(rows)), "older_cursor": older}


@router.post("/conversations/{conversation_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(conversation_id: UUID, ctx: TenantContext = Depends(MEMBER)) -> None:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        await s.execute(dq.MARK_READ, {"id": conversation_id})


# ------------------------------------------------------------------ control
async def _lock(s, conversation_id: UUID) -> dict:
    row = (await s.execute(dq.LOCK_FOR_STAFF, {"id": conversation_id})).mappings().first()
    if row is None:
        raise _not_found()
    return dict(row)


async def _takeover(s, conversation_id: UUID) -> None:
    await s.execute(dq.TAKEOVER, {"id": conversation_id})
    await s.execute(dq.CANCEL_PENDING_BOT_REPLIES, {"id": conversation_id})


@router.post("/conversations/{conversation_id}/takeover")
async def takeover(conversation_id: UUID, ctx: TenantContext = Depends(MEMBER)) -> dict:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        conv = await _lock(s, conversation_id)
        if conv["mode"] == "closed":
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "conversation_closed"})
        await _takeover(s, conversation_id)
    return {"id": conversation_id, "mode": "human"}


@router.post("/conversations/{conversation_id}/release")
async def release(conversation_id: UUID, ctx: TenantContext = Depends(MEMBER)) -> dict:
    """إرجاع المحادثة للبوت. رسائل الزبون التي لم يُرد عليها => البوت يرد عليها الآن."""
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        conv = await _lock(s, conversation_id)
        if conv["mode"] == "closed":
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "conversation_closed"})
        await s.execute(dq.RELEASE, {"id": conversation_id})
    return {"id": conversation_id, "mode": "bot"}


@router.post("/conversations/{conversation_id}/close")
async def close(conversation_id: UUID, ctx: TenantContext = Depends(MEMBER)) -> dict:
    """رسالة جديدة من الزبون بعد الإغلاق تفتح محادثة جديدة تلقائياً."""
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        await _lock(s, conversation_id)
        await s.execute(dq.CLOSE, {"id": conversation_id})
        await s.execute(dq.CANCEL_PENDING_BOT_REPLIES, {"id": conversation_id})
    return {"id": conversation_id, "mode": "closed"}


@router.post("/conversations/{conversation_id}/assign")
async def assign(conversation_id: UUID, body: AssignIn, ctx: TenantContext = Depends(MEMBER)) -> dict:
    """الموظف يسند المحادثة لنفسه أو يلغي إسنادها؛ الإسناد لغيره للمدير فقط."""
    if body.user_id is not None and body.user_id != ctx.user.id and not ctx.at_least("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"error": "insufficient_role", "required": "admin"})
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        await _lock(s, conversation_id)
        ok = (await s.execute(dq.ASSIGN, {"id": conversation_id, "user_id": body.user_id})).first()
    if ok is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "user_not_member"})
    return {"id": conversation_id, "assigned_user_id": body.user_id}


# ------------------------------------------------------------------ staff reply
@router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(conversation_id: UUID, body: SendMessage, ctx: TenantContext = Depends(MEMBER)) -> dict:
    """رد الموظف: يستلم المحادثة تلقائياً إن كانت مع البوت، ويُرسل عبر نفس طابور الإرسال."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "empty_message"})
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        conv = await _lock(s, conversation_id)
        existing = (await s.execute(dq.MESSAGE_BY_CLIENT_ID,
                                    {"client_msg_id": body.client_msg_id})).mappings().first()
        if existing is not None:      # نفس الطلب وصل مرتين (إعادة المحاولة من الواجهة)
            return {"id": existing["id"], "created_at": existing["created_at"], "duplicate": True}
        if conv["mode"] == "closed":
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "conversation_closed"})
        if conv["channel_status"] != "active":
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "channel_not_active"})
        if not window_open(conv["last_inbound_at"]):
            raise HTTPException(status.HTTP_409_CONFLICT, {
                "error": "reply_window_closed",
                "message": "مرت أكثر من 24 ساعة على آخر رسالة من الزبون؛ لا يمكن إرسال رسالة حرة"})
        if conv["mode"] != "human":
            await _takeover(s, conversation_id)
        msg = (await s.execute(dq.INSERT_STAFF_MESSAGE, {
            "conversation_id": conversation_id, "channel": conv["channel"], "text_content": text,
            "client_msg_id": body.client_msg_id})).mappings().one()
        await s.execute(q.ENQUEUE_OUTBOUND, {
            "channel_account_id": conv["channel_account_id"], "recipient": conv["recipient"],
            "purpose": "staff_reply", "conversation_id": conversation_id, "message_id": msg["id"],
            "kind": "text", "body": json.dumps({"text": text}, ensure_ascii=False)})
        await s.execute(dq.MARK_CONVERSATION_ANSWERED, {"id": conversation_id})
    return {"id": msg["id"], "created_at": msg["created_at"], "delivery_status": "pending", "duplicate": False}


@router.post("/messages/{message_id}/retry")
async def retry_message(message_id: UUID, ctx: TenantContext = Depends(MEMBER)) -> dict:
    """إعادة إرسال رد موظف فشل (مثلاً انقطاع مؤقت). نافذة الـ 24 ساعة تُفحص عند الإرسال."""
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        ok = (await s.execute(dq.RETRY_STAFF_MESSAGE, {"message_id": message_id})).first()
    if ok is None:
        raise HTTPException(status.HTTP_409_CONFLICT, {"error": "not_retryable"})
    return {"id": message_id, "delivery_status": "pending"}


@router.get("/team/staff")
async def list_staff(ctx: TenantContext = Depends(MEMBER)) -> list[dict]:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        return [dict(r) for r in (await s.execute(dq.STAFF_LIST)).mappings().all()]

