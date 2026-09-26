"""Self-serve identity: register (user + organisation), login/logout, profile, team invitations."""
from __future__ import annotations

import re
import secrets
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.agent.phone import normalize_phone
from app.api.deps import ADMIN, CurrentUser, TenantContext, client_ip, current_user
from app.billing.errors import sqlstate_of
from app.core.config import get_settings
from app.db.tenant import system_session, tenant_session, user_session
from app.identity import queries as iq
from app.identity.security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    new_token,
    token_hash,
    verify_dummy,
    verify_password,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])

MAX_FAILURES_PER_EMAIL = 10
MAX_FAILURES_PER_IP = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterIn(Strict):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)
    full_name: str = Field(..., min_length=2, max_length=100)
    phone: str | None = Field(None, description="رقم واتساب لاستقبال إشعارات الـ Leads")
    business_name: str | None = Field(None, min_length=2, max_length=150,
                                      description="إلزامي إلا عند التسجيل بدعوة")
    invite_token: str | None = Field(None, min_length=20, description="الانضمام لنشاط قائم بدل إنشاء نشاط جديد")
    business_slug: str | None = Field(None, pattern=r"^[a-z0-9-]{3,50}$")
    business_type: Literal["travel_hajj_umrah", "retail", "services", "other"] = "travel_hajj_umrah"


class LoginIn(Strict):
    email: str
    password: str


class PasswordChangeIn(Strict):
    current_password: str
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)


class PasswordResetIn(Strict):
    token: str = Field(..., min_length=20)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)


class InvitationIn(Strict):
    role: Literal["admin", "agent"] = "agent"
    email: str | None = None
    note: str | None = Field(None, max_length=200)


class AcceptInvitationIn(Strict):
    token: str = Field(..., min_length=20)


# ------------------------------------------------------------------ helpers
def _issue_session_response(response: Response, request: Request, token: str) -> dict:
    """الويب: كوكي httpOnly فقط. الجوال (X-Auth-Mode: token): التوكن في الجسم."""
    settings = get_settings()
    if request.headers.get("X-Auth-Mode") == "token":
        return {"token": token}
    response.set_cookie(settings.session_cookie_name, token, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=settings.session_ttl_days * 86400, path="/")
    return {}


async def _rate_limit(s, email: str | None, ip: str | None) -> None:
    row = (await s.execute(iq.RECENT_FAILURES, {"email": email, "ip": ip})).mappings().one()
    if (row["by_email"] or 0) >= MAX_FAILURES_PER_EMAIL or (row["by_ip"] or 0) >= MAX_FAILURES_PER_IP:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            {"error": "too_many_attempts", "message": "محاولات كثيرة، حاول بعد ربع ساعة"})


async def _new_session(s, user_id: UUID, request: Request) -> str:
    token = new_token("ses")
    await s.execute(iq.INSERT_SESSION, {"user_id": user_id, "token_hash": token_hash(token),
                                        "days": get_settings().session_ttl_days, "ip": client_ip(request),
                                        "ua": (request.headers.get("User-Agent") or "")[:300]})
    return token


# ------------------------------------------------------------------ register / login
@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, request: Request, response: Response) -> dict:
    """مستخدم + نشاط + فترة تجريبية في transaction واحدة. البريد المكرر => 409."""
    settings = get_settings()
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "invalid_email"})
    phone = normalize_phone(body.phone) if body.phone else None
    if body.phone and phone is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "invalid_phone"})
    if not body.invite_token and not body.business_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "business_name_required"})
    slug = body.business_slug or f"org-{secrets.token_hex(4)}"
    ip = client_ip(request)

    try:
        async with system_session() as s:
            await _rate_limit(s, None, ip)
            user_id = (await s.execute(iq.INSERT_USER, {
                "email": email, "full_name": body.full_name.strip(), "phone": phone,
                "password_hash": hash_password(body.password)})).scalar_one()
            await s.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(user_id)})
            if body.invite_token:
                # موظف/مالك مدعو: ينضم لنشاط قائم (نفس الـ transaction => لا حساب يتيم عند دعوة خاطئة)
                tenant_id = (await s.execute(iq.ACCEPT_INVITATION,
                                             {"h": token_hash(body.invite_token)})).mappings().one()["tenant_id"]
                slug = None
            else:
                tenant_id = (await s.execute(iq.REGISTER_TENANT, {
                    "slug": slug, "name": body.business_name.strip(), "business_type": body.business_type,
                    "plan_code": settings.signup_plan_code, "trial_days": settings.signup_trial_days,
                    "owner_phone": phone})).scalar_one()
            token = await _new_session(s, user_id, request)
            await s.execute(iq.RECORD_ATTEMPT, {"email": email, "ip": ip, "success": True})
    except HTTPException:
        raise
    except Exception as exc:
        code = sqlstate_of(exc)
        if code == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "email_or_slug_taken"}) from None
        if code == "AU002":
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "invalid_invitation"}) from None
        if code == "BL005":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"error": "signup_plan_not_configured"}) from None
        raise
    return {"user_id": user_id, "tenant_id": tenant_id, "tenant_slug": slug,
            **_issue_session_response(response, request, token)}


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict:
    email = body.email.strip().lower()
    ip = client_ip(request)
    async with system_session() as s:
        await _rate_limit(s, email, ip)
        user = (await s.execute(iq.USER_BY_EMAIL, {"email": email})).mappings().first()
        if user is None:
            verify_dummy(body.password)     # نفس الزمن تقريباً: لا نكشف وجود البريد
            ok = False
        else:
            ok = user["is_active"] and verify_password(body.password, user["password_hash"])
        await s.execute(iq.RECORD_ATTEMPT, {"email": email, "ip": ip, "success": ok})
        if not ok:
            invalid = True
        else:
            invalid = False
            if needs_rehash(user["password_hash"]):
                await s.execute(iq.UPDATE_PASSWORD, {"id": user["id"], "h": hash_password(body.password)})
            await s.execute(iq.TOUCH_LOGIN, {"id": user["id"]})
            token = await _new_session(s, user["id"], request)
    # سجل المحاولة الفاشلة محفوظ (commit) قبل الرد بالخطأ
    if invalid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            {"error": "invalid_credentials", "message": "البريد أو كلمة المرور غير صحيحة"})
    return {"user_id": user["id"], **_issue_session_response(response, request, token)}


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, user: CurrentUser = Depends(current_user)) -> Response:
    async with system_session() as s:
        await s.execute(iq.REVOKE_SESSION, {"id": user.session_id})
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/auth/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: CurrentUser = Depends(current_user)) -> Response:
    async with system_session() as s:
        await s.execute(iq.REVOKE_ALL_SESSIONS, {"user_id": user.id, "keep": None})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/password")
async def change_password(body: PasswordChangeIn, user: CurrentUser = Depends(current_user)) -> dict:
    """تغيير كلمة المرور يلغي كل الجلسات الأخرى."""
    async with system_session() as s:
        row = (await s.execute(iq.USER_BY_EMAIL, {"email": user.email})).mappings().one()
        if not verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, {"error": "invalid_credentials"})
        await s.execute(iq.UPDATE_PASSWORD, {"id": user.id, "h": hash_password(body.new_password)})
        await s.execute(iq.REVOKE_ALL_SESSIONS, {"user_id": user.id, "keep": user.session_id})
    return {"status": "ok"}


@router.post("/auth/password-reset")
async def reset_password(body: PasswordResetIn, request: Request) -> dict:
    """رابط لمرة واحدة يصدره الدعم (لا يوجد بريد بعد). ينجح => كل الجلسات تُلغى ويدخل المستخدم من جديد."""
    ip = client_ip(request)
    failed = False
    async with system_session() as s:
        await _rate_limit(s, None, ip)
        try:
            async with s.begin_nested():
                await s.execute(text("SELECT consume_password_reset(:h, :p)"),
                                {"h": token_hash(body.token), "p": hash_password(body.new_password)})
        except Exception as exc:
            if sqlstate_of(exc) != "AU003":
                raise
            failed = True
            await s.execute(iq.RECORD_ATTEMPT, {"email": None, "ip": ip, "success": False})
    if failed:   # سجل المحاولة الفاشلة محفوظ قبل الرد
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"error": "invalid_reset_token", "message": "الرابط غير صالح أو مستخدم أو منتهي"})
    return {"status": "ok"}


@router.get("/me")
async def me(user: CurrentUser = Depends(current_user)) -> dict:
    async with user_session(user.id) as s:
        tenants = (await s.execute(iq.MY_TENANTS)).mappings().all()
    return {"user": {"id": user.id, "email": user.email, "full_name": user.full_name,
                     "phone": user.phone_e164},
            "tenants": [dict(t) for t in tenants]}


# ------------------------------------------------------------------ team
@router.get("/team/members")
async def list_members(ctx: TenantContext = Depends(ADMIN)) -> list[dict]:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        return [dict(r) for r in (await s.execute(iq.LIST_MEMBERS)).mappings().all()]


@router.post("/team/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(body: InvitationIn, ctx: TenantContext = Depends(ADMIN)) -> dict:
    """يعيد رابط/توكن الدعوة مرة واحدة (يُرسل للموظف عبر واتساب). admin لا يدعو admin إلا المالك."""
    if body.role == "admin" and ctx.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"error": "only_owner_invites_admins"})
    token = new_token("inv")
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        row = (await s.execute(iq.CREATE_INVITATION, {
            "role": body.role, "h": token_hash(token), "email": body.email, "note": body.note,
            "days": get_settings().invitation_ttl_days})).mappings().one()
    return {"id": row["id"], "token": token, "expires_at": row["expires_at"]}


@router.get("/team/invitations")
async def list_invitations(ctx: TenantContext = Depends(ADMIN)) -> list[dict]:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        return [dict(r) for r in (await s.execute(iq.LIST_INVITATIONS)).mappings().all()]


@router.delete("/team/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(invitation_id: UUID, ctx: TenantContext = Depends(ADMIN)) -> Response:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        if (await s.execute(iq.REVOKE_INVITATION, {"id": invitation_id})).first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "invitation_not_found"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/team/invitations/accept")
async def accept_invitation(body: AcceptInvitationIn, user: CurrentUser = Depends(current_user)) -> dict:
    try:
        async with user_session(user.id) as s:
            row = (await s.execute(iq.ACCEPT_INVITATION, {"h": token_hash(body.token)})).mappings().one()
    except Exception as exc:
        if sqlstate_of(exc) == "AU002":
            raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "invalid_invitation"}) from None
        raise
    return {"tenant_id": row["tenant_id"], "role": row["role"]}

