"""Leads CRM: قائمة الطلبات، التفاصيل + السجل، تغيير الحالة/الإسناد/الملاحظات، تصدير CSV.

كل تغيير يُسجَّل في lead_events داخل نفس الـ transaction (قفل صف الطلب => لا تضيع تعديلات متزامنة).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ADMIN, MEMBER, TenantContext
from app.dashboard import queries as dq
from app.dashboard.logic import (
    LEAD_STATUSES,
    LeadPatch,
    LeadPatchError,
    apply_lead_patch,
    decode_cursor,
    leads_to_csv,
    next_cursor,
)
from app.db import queries as q
from app.db.tenant import tenant_session

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])

LeadStatus = Literal["new", "contacted", "qualified", "booked", "lost", "spam"]
assert set(LEAD_STATUSES) == set(LeadStatus.__args__)  # type: ignore[attr-defined]


class LeadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: LeadStatus | None = None
    lost_reason: str | None = Field(None, max_length=500)
    assigned_to: UUID | None = Field(None, description="staff_users.id")
    unassign: bool = False
    notes: str | None = Field(None, max_length=4000)


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=1, max_length=2000)


def filter_params(status_: str | None, assigned: str, package_id: UUID | None, q_: str | None,
                  from_date: date | None, to_date: date | None) -> dict:
    """assigned: any | unassigned | <staff_users.id>"""
    if assigned in ("any", "unassigned"):
        mode, staff = assigned, None
    else:
        try:
            mode, staff = "staff", UUID(assigned)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "invalid_assigned"}) from None
    return {"status": status_, "assigned_mode": mode, "assigned_staff": staff, "package_id": package_id,
            "q": (q_ or "").strip() or None, "from_date": from_date, "to_date": to_date}


@router.get("")
async def list_leads(status_: LeadStatus | None = Query(None, alias="status"), assigned: str = "any",
                     package_id: UUID | None = None, q_: str | None = Query(None, alias="q", max_length=100),
                     from_date: date | None = None, to_date: date | None = None,
                     cursor: str | None = None, limit: int = Query(30, ge=1, le=100),
                     ctx: TenantContext = Depends(MEMBER)) -> dict:
    try:
        ts, cid = decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"error": "invalid_cursor"}) from None
    params = filter_params(status_, assigned, package_id, q_, from_date, to_date)
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        rows = [dict(r) for r in (await s.execute(dq.LEADS_LIST, {
            **params, "cursor_ts": ts, "cursor_id": cid, "limit": limit})).mappings().all()]
    return {"items": rows, "next_cursor": next_cursor(rows, limit, "created_at")}


@router.get("/export.csv")
async def export_leads(status_: LeadStatus | None = Query(None, alias="status"), assigned: str = "any",
                       package_id: UUID | None = None, q_: str | None = Query(None, alias="q", max_length=100),
                       from_date: date | None = None, to_date: date | None = None,
                       ctx: TenantContext = Depends(ADMIN)) -> Response:
    """تصدير (مدير أو مالك فقط: بيانات زبائن بالجملة). بحد أقصى 10,000 صف."""
    params = filter_params(status_, assigned, package_id, q_, from_date, to_date)
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        tz_name = (await s.execute(q.LOAD_TENANT_PROFILE)).mappings().one()["timezone"]
        rows = [dict(r) for r in (await s.execute(dq.LEADS_EXPORT, params)).mappings().all()]
    try:
        tz = ZoneInfo(tz_name or "Africa/Tripoli")
    except Exception:  # noqa: BLE001 - اسم منطقة غير صالح في الإعدادات لا يمنع التصدير
        tz = ZoneInfo("Africa/Tripoli")
    name = f"leads-{datetime.now(tz):%Y%m%d-%H%M}.csv"
    return Response(leads_to_csv(rows, tz), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"',
                             "Cache-Control": "no-store"})


@router.get("/{lead_id}")
async def get_lead(lead_id: UUID, ctx: TenantContext = Depends(MEMBER)) -> dict:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        row = (await s.execute(dq.LEAD_DETAIL, {"id": lead_id})).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "lead_not_found"})
        events = [dict(e) for e in (await s.execute(dq.LEAD_EVENTS, {"id": lead_id})).mappings().all()]
    return {**dict(row), "events": events}


@router.patch("/{lead_id}")
async def update_lead(lead_id: UUID, body: LeadUpdate, ctx: TenantContext = Depends(MEMBER)) -> dict:
    patch = LeadPatch(status=body.status, lost_reason=body.lost_reason, assigned_to=body.assigned_to,
                      unassign=body.unassign, notes=body.notes)
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        current = (await s.execute(dq.LEAD_FOR_UPDATE, {"id": lead_id})).mappings().first()
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "lead_not_found"})
        try:
            new, events = apply_lead_patch(dict(current), patch)
        except LeadPatchError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": str(e)}) from None
        if new["assigned_to"] is not None and new["assigned_to"] != current["assigned_to"]:
            if (await s.execute(dq.ACTIVE_STAFF_EXISTS, {"id": new["assigned_to"]})).first() is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "staff_not_found"})
        if events:
            await s.execute(dq.UPDATE_LEAD, {"id": lead_id, **new})
            for event_type, data in events:
                await s.execute(dq.INSERT_LEAD_EVENT, {"lead_id": lead_id, "event_type": event_type,
                                                       "data": json.dumps(data, ensure_ascii=False)})
        row = (await s.execute(dq.LEAD_DETAIL, {"id": lead_id})).mappings().one()
    return {**dict(row), "changed": [e for e, _ in events]}


@router.post("/{lead_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(lead_id: UUID, body: NoteIn, ctx: TenantContext = Depends(MEMBER)) -> dict:
    """ملاحظة متابعة في سجل الطلب (مثلاً: «اتصلت، يبي يأكد بعد الراتب»)."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "empty_note"})
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        if (await s.execute(dq.LEAD_FOR_UPDATE, {"id": lead_id})).first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "lead_not_found"})
        await s.execute(dq.INSERT_LEAD_EVENT, {"lead_id": lead_id, "event_type": "note",
                                               "data": json.dumps({"text": text}, ensure_ascii=False)})
    return {"lead_id": lead_id, "event_type": "note"}
