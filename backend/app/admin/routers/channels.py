"""Assisted onboarding: connect WhatsApp numbers and Facebook pages (+ linked Instagram) for an office
using tokens obtained manually (System User token / Page token), before Embedded Signup exists.

كل ربط: تحقق من التوكن عند Meta أولاً => اشتراك الـ webhooks => حفظ مشفّر. لا شيء يُحفظ إذا فشل التحقق.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text

from app.admin.auth import SUPPORT, Admin, audit
from app.admin.schemas import ChannelStatusUpdate, FacebookPageConnect, WhatsAppConnect
from app.channels.meta_graph import MetaGraphClient, MetaGraphError
from app.core.config import get_settings
from app.core.crypto import decrypt_token, encrypt_token
from app.db.admin import admin_session

router = APIRouter(prefix="/admin", tags=["channels"])

# لا ننقل رقماً/صفحة من وكالة لأخرى بصمت: WHERE يمنع التحديث إذا اختلفت الوكالة
_UPSERT = text("""
    INSERT INTO channel_accounts (tenant_id, channel, external_id, waba_id, display_name,
                                  access_token_enc, is_test, status, config, last_checked_at)
    VALUES (:t, :channel, :external_id, :waba_id, :display_name, :token, :is_test, 'active',
            CAST(:config AS jsonb), now())
    ON CONFLICT (channel, external_id) DO UPDATE
       SET access_token_enc = EXCLUDED.access_token_enc,
           waba_id = coalesce(EXCLUDED.waba_id, channel_accounts.waba_id),
           display_name = EXCLUDED.display_name,
           is_test = EXCLUDED.is_test,
           status = 'active', last_error = NULL, last_checked_at = now(),
           config = channel_accounts.config || EXCLUDED.config
     WHERE channel_accounts.tenant_id = EXCLUDED.tenant_id
    RETURNING id
""")


def graph(request: Request) -> MetaGraphClient:
    return request.app.state.graph


def _meta_error(exc: MetaGraphError, step: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
        "error": "meta_rejected", "step": step, "meta_code": exc.code, "message": str(exc.args[0])})


async def _ensure_tenant(s: Any, tenant_id: UUID) -> None:
    if (await s.execute(text("SELECT 1 FROM tenants WHERE id = :t"), {"t": tenant_id})).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")


async def _upsert(s: Any, **params: Any) -> UUID:
    row = (await s.execute(_UPSERT, params)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "error": "channel_owned_by_another_tenant", "channel": params["channel"],
            "external_id": params["external_id"]})
    return row


@router.post("/tenants/{tenant_id}/channels/whatsapp", status_code=status.HTTP_201_CREATED)
async def connect_whatsapp(tenant_id: UUID, body: WhatsAppConnect, admin: Admin = Depends(SUPPORT),
                           g: MetaGraphClient = Depends(graph)) -> dict:
    try:
        info = await g.get_phone_number(body.phone_number_id, body.access_token)
    except MetaGraphError as exc:
        raise _meta_error(exc, "verify_phone_number") from exc
    subscribed = False
    if body.subscribe_webhooks and body.waba_id:
        try:
            await g.subscribe_waba(body.waba_id, body.access_token)
            subscribed = True
        except MetaGraphError as exc:
            raise _meta_error(exc, "subscribe_waba_webhooks") from exc

    display = f"{info.get('verified_name', '')} {info.get('display_phone_number', '')}".strip()
    async with admin_session() as s:
        await _ensure_tenant(s, tenant_id)
        channel_id = await _upsert(
            s, t=tenant_id, channel="whatsapp", external_id=body.phone_number_id, waba_id=body.waba_id,
            display_name=display or None, token=encrypt_token(body.access_token), is_test=body.is_test,
            config=json.dumps({"quality_rating": info.get("quality_rating"),
                               "name_status": info.get("name_status"),
                               "onboarding": "assisted"}, ensure_ascii=False))
        await audit(s, admin, "channel.connect.whatsapp", tenant_id=tenant_id, target_type="channel",
                    target_id=channel_id, details={"phone_number_id": body.phone_number_id,
                                                   "waba_id": body.waba_id, "webhooks": subscribed})
    return {"id": channel_id, "display_name": display, "webhooks_subscribed": subscribed,
            "quality_rating": info.get("quality_rating")}


@router.post("/tenants/{tenant_id}/channels/facebook-page", status_code=status.HTTP_201_CREATED)
async def connect_facebook_page(tenant_id: UUID, body: FacebookPageConnect,
                                admin: Admin = Depends(SUPPORT),
                                g: MetaGraphClient = Depends(graph)) -> dict:
    try:
        page = await g.get_page(body.page_id, body.page_access_token)
        if body.subscribe_webhooks:
            await g.subscribe_page(body.page_id, body.page_access_token)
    except MetaGraphError as exc:
        raise _meta_error(exc, "verify_or_subscribe_page") from exc

    ig = page.get("instagram_business_account") or {}
    token = encrypt_token(body.page_access_token)
    created: list[dict[str, Any]] = []
    async with admin_session() as s:
        await _ensure_tenant(s, tenant_id)
        mid = await _upsert(s, t=tenant_id, channel="messenger", external_id=body.page_id, waba_id=None,
                            display_name=page.get("name"), token=token, is_test=False,
                            config=json.dumps({"onboarding": "assisted"}))
        created.append({"id": mid, "channel": "messenger", "name": page.get("name")})
        if body.include_instagram and ig.get("id"):
            iid = await _upsert(s, t=tenant_id, channel="instagram", external_id=ig["id"], waba_id=None,
                                display_name=f"@{ig.get('username')}", token=token, is_test=False,
                                config=json.dumps({"page_id": body.page_id, "onboarding": "assisted"}))
            created.append({"id": iid, "channel": "instagram", "name": f"@{ig.get('username')}"})
        await audit(s, admin, "channel.connect.facebook_page", tenant_id=tenant_id, target_type="page",
                    target_id=body.page_id, details={"channels": [c["channel"] for c in created]})
    return {"channels": created,
            "instagram_linked": bool(ig.get("id")),
            "note": "أحداث ماسنجر/إنستغرام تُحفظ خاماً في webhook_events؛ المعالجة والرد الآلي بعد إضافة الـ parsers (المرحلة 5)."}


@router.get("/tenants/{tenant_id}/channels")
async def list_channels(tenant_id: UUID, _: Admin = Depends(SUPPORT)) -> list[dict]:
    async with admin_session() as s:
        rows = (await s.execute(text("""
            SELECT id, channel, external_id, waba_id, display_name, status, is_test, config,
                   last_checked_at, last_error, created_at
              FROM channel_accounts WHERE tenant_id = :t ORDER BY created_at
        """), {"t": tenant_id})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/channels/{channel_id}/check")
async def check_channel(channel_id: UUID, admin: Admin = Depends(SUPPORT),
                        g: MetaGraphClient = Depends(graph)) -> dict:
    """فحص صحة التوكن الآن. الفشل بـ 190 (توكن ملغى/منتهي) => needs_reauth."""
    async with admin_session() as s:
        ch = (await s.execute(text("""
            SELECT id, tenant_id, channel, external_id, access_token_enc, config
              FROM channel_accounts WHERE id = :id
        """), {"id": channel_id})).mappings().first()
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel not found")

    settings = get_settings()
    result: dict[str, Any] = {"ok": True}
    new_status, error = "active", None
    try:
        token = decrypt_token(ch["access_token_enc"])
        if ch["channel"] == "whatsapp":
            result["meta"] = await g.get_phone_number(ch["external_id"], token)
        else:
            page_id = (ch["config"] or {}).get("page_id") or ch["external_id"]
            result["meta"] = await g.get_page(page_id, token)
        if settings.meta_app_id:
            app_token = f"{settings.meta_app_id}|{settings.meta_app_secret.get_secret_value()}"
            dbg = await g.debug_token(token, app_token)
            result["token"] = {"is_valid": dbg.get("is_valid"), "expires_at": dbg.get("expires_at"),
                               "scopes": dbg.get("scopes")}
            if dbg.get("is_valid") is False:
                new_status, error = "needs_reauth", "token invalid (debug_token)"
    except MetaGraphError as exc:
        result["ok"] = False
        error = f"{exc.code}: {exc.args[0]}"[:500]
        new_status = "needs_reauth" if exc.code == 190 else "active"
    except Exception as exc:  # noqa: BLE001 — مثلاً فشل فك التشفير
        result["ok"] = False
        error, new_status = f"{type(exc).__name__}: {exc}"[:500], "needs_reauth"

    async with admin_session() as s:
        await s.execute(text("""
            UPDATE channel_accounts SET last_checked_at = now(), last_error = :err,
                   status = CASE WHEN status IN ('paused','disconnected') THEN status ELSE :st END
             WHERE id = :id
        """), {"id": channel_id, "err": error, "st": new_status})
        await audit(s, admin, "channel.check", tenant_id=ch["tenant_id"], target_type="channel",
                    target_id=channel_id, details={"ok": result["ok"], "error": error})
    return {**result, "status": new_status, "error": error}


@router.post("/channels/{channel_id}/status")
async def set_channel_status(channel_id: UUID, body: ChannelStatusUpdate,
                             admin: Admin = Depends(SUPPORT)) -> dict:
    async with admin_session() as s:
        tenant_id = (await s.execute(text("""
            UPDATE channel_accounts SET status = :st WHERE id = :id RETURNING tenant_id
        """), {"id": channel_id, "st": body.status})).scalar_one_or_none()
        if tenant_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "channel not found")
        await audit(s, admin, "channel.status", tenant_id=tenant_id, target_type="channel",
                    target_id=channel_id, details={"status": body.status})
    return {"id": channel_id, "status": body.status}
