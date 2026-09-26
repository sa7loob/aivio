"""Tenants (offices): manual registration, listing, settings, staff."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text

from app.admin.auth import ANY_ADMIN, SUPPORT, Admin, audit
from app.admin.schemas import OwnerInvitationCreate, PasswordResetIssue, StaffCreate, TenantCreate, TenantUpdate, money
from app.agent.phone import normalize_phone
from app.core.config import get_settings
from app.identity.security import new_token, token_hash
from app.db.admin import admin_session

router = APIRouter(prefix="/admin/tenants", tags=["tenants"])


def _phone_or_422(raw: str | None) -> str | None:
    if raw is None:
        return None
    phone = normalize_phone(raw)
    if phone is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "invalid_phone", "value": raw})
    return phone


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tenant(body: TenantCreate, admin: Admin = Depends(SUPPORT)) -> dict:
    """تسجيل مكتب يدوياً: الوكالة + المالك كموظف يستقبل الإشعارات + اشتراك تجريبي."""
    owner_phone = _phone_or_422(body.owner.whatsapp_phone)
    async with admin_session() as s:
        plan = (await s.execute(text("""
            SELECT p.id, (SELECT pp.id FROM plan_prices pp
                           WHERE pp.plan_id = p.id AND pp.is_active ORDER BY pp.period_months LIMIT 1) AS price_id
              FROM plans p WHERE p.code = :code AND p.is_active
        """), {"code": body.plan_code})).mappings().first()
        if plan is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "unknown_plan"})

        tenant_id = (await s.execute(text("""
            INSERT INTO tenants (slug, name, business_type, status, timezone, settings)
            VALUES (:slug, :name, :bt, 'trial', :tz, CAST(:settings AS jsonb)) RETURNING id
        """), {"slug": body.slug, "name": body.name, "bt": body.business_type, "tz": body.timezone,
               "settings": json.dumps(body.settings, ensure_ascii=False)})).scalar_one()

        await s.execute(text("""
            INSERT INTO staff_users (tenant_id, full_name, email, whatsapp_phone, role, notify_on_new_lead)
            VALUES (:t, :name, :email, :phone, 'owner', true)
        """), {"t": tenant_id, "name": body.owner.full_name, "email": body.owner.email, "phone": owner_phone})

        await s.execute(text("""
            INSERT INTO subscriptions (tenant_id, plan_id, status, current_period_end, renewal_price_id)
            VALUES (:t, :plan, 'trialing', now() + make_interval(days => :days), :price)
        """), {"t": tenant_id, "plan": plan["id"], "days": body.trial_days, "price": plan["price_id"]})

        await audit(s, admin, "tenant.create", tenant_id=tenant_id, target_type="tenant",
                    target_id=tenant_id, details={"slug": body.slug, "plan": body.plan_code,
                                                  "trial_days": body.trial_days})
    return {"id": tenant_id, "slug": body.slug}


@router.get("")
async def list_tenants(q: str | None = None, subscription_status: str | None = None,
                       limit: int = Query(50, le=200), offset: int = 0,
                       _: Admin = Depends(ANY_ADMIN)) -> list[dict]:
    async with admin_session() as s:
        rows = (await s.execute(text("""
            SELECT t.id, t.slug, t.name, t.status, t.business_type, t.created_at,
                   s.status AS subscription_status, s.current_period_end, pl.code AS plan_code,
                   coalesce(w.balance_lyd, 0) AS balance_lyd,
                   (SELECT count(*) FROM channel_accounts ca WHERE ca.tenant_id = t.id AND ca.status = 'active') AS active_channels,
                   (SELECT count(*) FROM leads l WHERE l.tenant_id = t.id AND l.created_at > now() - interval '30 days') AS leads_30d
              FROM tenants t
              LEFT JOIN subscriptions s ON s.tenant_id = t.id
              LEFT JOIN plans pl ON pl.id = s.plan_id
              LEFT JOIN wallets w ON w.tenant_id = t.id
             WHERE (CAST(:q AS text) IS NULL OR t.name ILIKE '%' || CAST(:q AS text) || '%'
                    OR t.slug ILIKE '%' || CAST(:q AS text) || '%')
               AND (CAST(:ss AS text) IS NULL OR s.status = CAST(:ss AS text))
             ORDER BY t.created_at DESC LIMIT :limit OFFSET :offset
        """), {"q": q, "ss": subscription_status, "limit": limit, "offset": offset})).mappings().all()
    return [{**r, "balance_lyd": money(r["balance_lyd"])} for r in rows]


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: UUID, _: Admin = Depends(ANY_ADMIN)) -> dict:
    p = {"t": tenant_id}
    async with admin_session() as s:
        tenant = (await s.execute(text("SELECT * FROM tenants WHERE id = :t"), p)).mappings().first()
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
        sub = (await s.execute(text("""
            SELECT s.*, pl.code AS plan_code, pl.name AS plan_name FROM subscriptions s
              JOIN plans pl ON pl.id = s.plan_id WHERE s.tenant_id = :t
        """), p)).mappings().first()
        balance = (await s.execute(text("SELECT balance_lyd FROM wallets WHERE tenant_id = :t"), p)).scalar()
        staff = (await s.execute(text("""
            SELECT id, full_name, email, whatsapp_phone, role, notify_on_new_lead, is_active
              FROM staff_users WHERE tenant_id = :t ORDER BY created_at
        """), p)).mappings().all()
        channels = (await s.execute(text("""
            SELECT id, channel, external_id, display_name, status, is_test, last_checked_at, last_error, created_at
              FROM channel_accounts WHERE tenant_id = :t ORDER BY created_at
        """), p)).mappings().all()
    return {"tenant": dict(tenant), "subscription": dict(sub) if sub else None,
            "balance_lyd": money(balance or 0), "staff": [dict(x) for x in staff],
            "channels": [dict(c) for c in channels]}


@router.patch("/{tenant_id}")
async def update_tenant(tenant_id: UUID, body: TenantUpdate, admin: Admin = Depends(SUPPORT)) -> dict:
    async with admin_session() as s:
        row = (await s.execute(text("""
            UPDATE tenants
               SET name = coalesce(:name, name),
                   settings = settings || CAST(:settings AS jsonb),
                   status = coalesce(:status, status)
             WHERE id = :t RETURNING id, name, status, settings
        """), {"t": tenant_id, "name": body.name, "status": body.status,
               "settings": json.dumps(body.settings or {}, ensure_ascii=False)})).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
        await audit(s, admin, "tenant.update", tenant_id=tenant_id, target_type="tenant",
                    target_id=tenant_id, details=body.model_dump(exclude_none=True))
    return dict(row)


@router.post("/{tenant_id}/staff", status_code=status.HTTP_201_CREATED)
async def add_staff(tenant_id: UUID, body: StaffCreate, admin: Admin = Depends(SUPPORT)) -> dict:
    phone = _phone_or_422(body.whatsapp_phone)
    async with admin_session() as s:
        staff_id = (await s.execute(text("""
            INSERT INTO staff_users (tenant_id, full_name, email, whatsapp_phone, role, notify_on_new_lead)
            VALUES (:t, :name, :email, :phone, :role, :notify) RETURNING id
        """), {"t": tenant_id, "name": body.full_name, "email": body.email, "phone": phone,
               "role": body.role, "notify": body.notify_on_new_lead})).scalar_one()
        await audit(s, admin, "staff.create", tenant_id=tenant_id, target_type="staff",
                    target_id=staff_id, details={"role": body.role})
    return {"id": staff_id}


@router.post("/{tenant_id}/owner-invitation", status_code=status.HTTP_201_CREATED)
async def create_owner_invitation(tenant_id: UUID, body: OwnerInvitationCreate,
                                  admin: Admin = Depends(SUPPORT)) -> dict:
    """يربط مكتباً سُجّل يدوياً بحساب دخول: المالك يسجّل (أو يدخل) ثم يقبل الدعوة من /connect.
    التوكن يظهر مرة واحدة ويُرسل للمالك عبر واتساب."""
    token = new_token("inv")
    async with admin_session() as s:
        if (await s.execute(text("SELECT 1 FROM tenants WHERE id = :t"), {"t": tenant_id})).first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
        row = (await s.execute(text("""
            INSERT INTO invitations (tenant_id, role, token_hash, email, note, expires_at)
            VALUES (:t, 'owner', :h, :email, :note, now() + make_interval(days => :days))
            RETURNING id, expires_at
        """), {"t": tenant_id, "h": token_hash(token), "email": body.email, "note": body.note,
               "days": get_settings().invitation_ttl_days})).mappings().one()
        await audit(s, admin, "tenant.owner_invitation", tenant_id=tenant_id, target_type="invitation",
                    target_id=row["id"], details={"email": body.email})
    return {"id": row["id"], "token": token, "expires_at": row["expires_at"]}


users_router = APIRouter(prefix="/admin/users", tags=["users"])


@users_router.post("/password-reset", status_code=status.HTTP_201_CREATED)
async def issue_password_reset(body: PasswordResetIssue, admin: Admin = Depends(SUPPORT)) -> dict:
    """رابط إعادة تعيين لمرة واحدة، يُرسل لصاحب الحساب عبر واتساب بعد التحقق من هويته."""
    settings = get_settings()
    token = new_token("rst")
    async with admin_session() as s:
        user_id = (await s.execute(text("SELECT id FROM users WHERE lower(email) = lower(:e) AND is_active"),
                                   {"e": body.email.strip()})).scalar_one_or_none()
        if user_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        row = (await s.execute(text("""
            INSERT INTO password_reset_tokens (user_id, token_hash, issued_by, expires_at)
            VALUES (:u, :h, :admin, now() + make_interval(hours => :hours)) RETURNING expires_at
        """), {"u": user_id, "h": token_hash(token), "admin": admin.id,
               "hours": settings.password_reset_ttl_hours})).mappings().one()
        await audit(s, admin, "user.password_reset_issued", target_type="user", target_id=user_id)
    return {"link": f"{settings.public_base_url.rstrip('/')}/connect?reset={token}",
            "expires_at": row["expires_at"]}
