"""GET /api/v1/events — Server-Sent Events للوحة (EventSource في المتصفح).

- المصادقة والوكالة مثل باقي الـ API (كوكي الجلسة + X-Tenant-ID أو ?tenant=).
  EventSource لا يرسل هيدرات مخصصة => الوكالة تُمرَّر كـ query param وتُتحقق عضويتها بنفس الطريقة.
- الأحداث معرّفات فقط: {"type":"message","id":..,"conversation_id":..}. الواجهة تجلب التفاصيل عبر الـ API.
- نبضة كل 20 ثانية (تبقي الـ proxy مفتوحاً)، والبث يُغلق بعد 30 دقيقة => المتصفح يعيد الاتصال تلقائياً
  ويُعاد فحص الجلسة والعضوية (جلسة ملغاة أو موظف محذوف لا يبقى متصلاً).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import MEMBER, TenantContext
from app.realtime.hub import EventHub
from app.realtime.sse import stream

router = APIRouter(prefix="/api/v1", tags=["realtime"])


@router.get("/events")
async def events(request: Request, ctx: TenantContext = Depends(MEMBER)) -> StreamingResponse:
    hub: EventHub | None = getattr(request.app.state, "hub", None)
    if hub is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"error": "realtime_unavailable"})
    return StreamingResponse(stream(request, hub, ctx), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
