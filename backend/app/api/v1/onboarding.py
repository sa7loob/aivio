"""Self-serve channel connection endpoints (owner/admin) + tenant channel list."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ADMIN, MEMBER, TenantContext
from app.channels.meta_graph import MetaGraphClient
from app.core.config import get_settings
from app.db.tenant import tenant_session
from app.identity import queries as iq
from app.onboarding.service import (
    FLOW_FACEBOOK,
    FLOW_WHATSAPP,
    Actor,
    OnboardingError,
    OnboardingService,
)

router = APIRouter(prefix="/api/v1", tags=["onboarding"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WhatsAppComplete(Strict):
    state: str = Field(..., min_length=10)
    code: str = Field(..., min_length=10)
    waba_id: str = Field(..., pattern=r"^\d{5,30}$")
    phone_number_id: str = Field(..., pattern=r"^\d{5,30}$")
    coexistence: bool = Field(False, description="رقم يعمل على تطبيق WhatsApp Business (featureType coexistence)")


class FacebookComplete(Strict):
    state: str = Field(..., min_length=10)
    code: str = Field(..., min_length=10)


class ConnectPages(Strict):
    page_ids: list[str] = Field(..., min_length=1, max_length=20)
    include_instagram: bool = True


def service(request: Request) -> OnboardingService:
    graph: MetaGraphClient = request.app.state.graph
    return OnboardingService(graph, get_settings(), tenant_session)


def _actor(ctx: TenantContext) -> Actor:
    return Actor(ctx.tenant_id, ctx.user.id)


async def _run(coro: Any) -> Any:
    try:
        return await coro
    except OnboardingError as exc:
        body: dict[str, Any] = {"error": exc.code, **exc.details}
        if exc.message:
            body["message"] = exc.message
        return JSONResponse(status_code=exc.http_status, content=body)


# ------------------------------------------------------------------ WhatsApp
@router.post("/onboarding/whatsapp/start", status_code=status.HTTP_201_CREATED)
async def whatsapp_start(ctx: TenantContext = Depends(ADMIN), svc: OnboardingService = Depends(service)):
    """يعيد ما تحتاجه الواجهة لتشغيل FB.login بـ config_id الخاص بـ Embedded Signup."""
    return await _run(svc.start(_actor(ctx), FLOW_WHATSAPP))


@router.post("/onboarding/whatsapp/{session_id}/complete")
async def whatsapp_complete(session_id: UUID, body: WhatsAppComplete, ctx: TenantContext = Depends(ADMIN),
                            svc: OnboardingService = Depends(service)):
    """يُستدعى فوراً من callback الـ SDK (صلاحية الـ code نحو 30 ثانية)."""
    return await _run(svc.complete_whatsapp(
        _actor(ctx), session_id, state=body.state, code=body.code, waba_id=body.waba_id,
        phone_number_id=body.phone_number_id, coexistence=body.coexistence))


@router.post("/onboarding/{session_id}/retry")
async def onboarding_retry(session_id: UUID, ctx: TenantContext = Depends(ADMIN),
                           svc: OnboardingService = Depends(service)):
    return await _run(svc.retry(_actor(ctx), session_id))


@router.get("/onboarding/{session_id}")
async def onboarding_status(session_id: UUID, ctx: TenantContext = Depends(ADMIN),
                            svc: OnboardingService = Depends(service)):
    return await _run(svc.status(_actor(ctx), session_id))


# ------------------------------------------------------------------ Facebook Pages + Instagram
@router.post("/onboarding/facebook/start", status_code=status.HTTP_201_CREATED)
async def facebook_start(ctx: TenantContext = Depends(ADMIN), svc: OnboardingService = Depends(service)):
    return await _run(svc.start(_actor(ctx), FLOW_FACEBOOK))


@router.post("/onboarding/facebook/{session_id}/complete")
async def facebook_complete(session_id: UUID, body: FacebookComplete, ctx: TenantContext = Depends(ADMIN),
                            svc: OnboardingService = Depends(service)):
    """يعيد قائمة الصفحات (مع إنستغرام المرتبط) ليختار المستخدم منها."""
    return await _run(svc.complete_facebook(_actor(ctx), session_id, state=body.state, code=body.code))


@router.post("/onboarding/facebook/{session_id}/connect")
async def facebook_connect(session_id: UUID, body: ConnectPages, ctx: TenantContext = Depends(ADMIN),
                           svc: OnboardingService = Depends(service)):
    return await _run(svc.connect_pages(_actor(ctx), session_id, body.page_ids, body.include_instagram))


# ------------------------------------------------------------------ channels of the current tenant
@router.get("/channels")
async def list_channels(ctx: TenantContext = Depends(MEMBER)) -> list[dict]:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        return [dict(r) for r in (await s.execute(iq.LIST_CHANNELS)).mappings().all()]


@router.post("/channels/{channel_id}/disconnect")
async def disconnect_channel(channel_id: UUID, ctx: TenantContext = Depends(ADMIN)) -> dict:
    """يوقف استقبال/إرسال الرسائل على هذه القناة فوراً (الأحداث القادمة تُتجاهل).
    لا نلغي اشتراك الـ webhook عند Meta تلقائياً: صفحة فيسبوك قد تخدم ماسنجر وإنستغرام معاً."""
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        row = (await s.execute(iq.CHANNEL_FOR_UPDATE, {"id": channel_id})).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "channel_not_found"})
        await s.execute(iq.SET_CHANNEL_STATUS, {"id": channel_id, "status": "disconnected",
                                                "error": f"disconnected by user {ctx.user.id}"})
    return {"id": channel_id, "status": "disconnected"}
