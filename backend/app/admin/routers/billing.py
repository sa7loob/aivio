"""Plans, wallets, subscriptions, manual payments, receipts.

كل عملية مالية = دالة قاعدة بيانات واحدة (wallet_apply / subscription_renew / ...) داخل transaction
مع سجل التدقيق. العمليات التي تُنشئ قيداً تتطلب هيدر Idempotency-Key (UUID) لمنع التكرار.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import text

from app.admin.auth import ANY_ADMIN, FINANCE, Admin, audit
from app.admin.schemas import (
    ManualPaymentCreate,
    ManualPaymentReject,
    PlanCreate,
    PlanPriceIn,
    PlanUpdate,
    SubscriptionGrant,
    SubscriptionRenew,
    SubscriptionUpdate,
    WalletAdjust,
    WalletCredit,
    money,
)
from app.db.admin import admin_session

router = APIRouter(prefix="/admin", tags=["billing"])

IdempotencyKey = Header(..., alias="Idempotency-Key", description="UUID يولّده العميل لكل عملية")

_WALLET_APPLY = text("""
    SELECT * FROM wallet_apply(:t, :direction, :amount, :source, :ref, :note, 'admin', :admin)
""")
_RECEIPT = text("SELECT * FROM issue_receipt(:ledger_id, :reference, :admin)")
_AUTORENEW = text("SELECT subscription_try_autorenew(:t)")


def _entry(e: Any) -> dict[str, Any]:
    return {"ledger_id": e["id"], "direction": e["direction"], "amount_lyd": money(e["amount_lyd"]),
            "balance_after": money(e["balance_after"]), "source": e["source"], "created_at": e["created_at"]}


async def credit_with_receipt(s: Any, admin: Admin, tenant_id: UUID, amount: Any, source: str,
                              reference_id: UUID, reference_text: str | None, note: str | None) -> dict[str, Any]:
    """شحن + سند قبض + محاولة تجديد تلقائي، في نفس الـ transaction. آمن لإعادة التنفيذ."""
    e = (await s.execute(_WALLET_APPLY, {"t": tenant_id, "direction": "credit", "amount": amount,
                                         "source": source, "ref": reference_id, "note": note,
                                         "admin": admin.id})).mappings().one()
    r = (await s.execute(_RECEIPT, {"ledger_id": e["id"], "reference": reference_text,
                                    "admin": admin.id})).mappings().one()
    renewed = (await s.execute(_AUTORENEW, {"t": tenant_id})).scalar_one()
    return {**_entry(e), "receipt_id": r["id"], "receipt_number": r["number"], "auto_renewed": renewed}


# =================================================================== plans
@router.get("/plans")
async def list_plans(_: Admin = Depends(ANY_ADMIN)) -> list[dict]:
    async with admin_session() as s:
        rows = (await s.execute(text("""
            SELECT p.id, p.code, p.name, p.description, p.limits, p.is_active, p.sort_order,
                   coalesce(json_agg(json_build_object('id', pp.id, 'period_months', pp.period_months,
                            'price_lyd', to_char(pp.price_lyd, 'FM9999999990.000'), 'is_active', pp.is_active)
                            ORDER BY pp.period_months) FILTER (WHERE pp.id IS NOT NULL), '[]') AS prices
              FROM plans p LEFT JOIN plan_prices pp ON pp.plan_id = p.id
             GROUP BY p.id ORDER BY p.sort_order, p.code
        """))).mappings().all()
    return [dict(r) for r in rows]


@router.post("/plans", status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanCreate, admin: Admin = Depends(FINANCE)) -> dict:
    async with admin_session() as s:
        plan_id = (await s.execute(text("""
            INSERT INTO plans (code, name, description, limits, sort_order)
            VALUES (:code, :name, :desc, CAST(:limits AS jsonb), :sort) RETURNING id
        """), {"code": body.code, "name": body.name, "desc": body.description,
               "limits": json.dumps(body.limits), "sort": body.sort_order})).scalar_one()
        for p in body.prices:
            await _upsert_price(s, plan_id, p)
        await audit(s, admin, "plan.create", target_type="plan", target_id=body.code,
                    details=body.model_dump(mode="json"))
    return {"id": plan_id, "code": body.code}


async def _upsert_price(s: Any, plan_id: UUID, p: PlanPriceIn) -> None:
    # السعر المرتبط باشتراكات قائمة يتغير للتجديدات القادمة فقط (الفترات المدفوعة محفوظة بمبلغها)
    await s.execute(text("""
        INSERT INTO plan_prices (plan_id, period_months, price_lyd, is_active)
        VALUES (:plan, :months, :price, :active)
        ON CONFLICT (plan_id, period_months) DO UPDATE
           SET price_lyd = EXCLUDED.price_lyd, is_active = EXCLUDED.is_active
    """), {"plan": plan_id, "months": p.period_months, "price": p.price_lyd, "active": p.is_active})


@router.patch("/plans/{code}")
async def update_plan(code: str, body: PlanUpdate, admin: Admin = Depends(FINANCE)) -> dict:
    async with admin_session() as s:
        row = (await s.execute(text("""
            UPDATE plans SET name = coalesce(:name, name), description = coalesce(:desc, description),
                   limits = coalesce(CAST(:limits AS jsonb), limits), is_active = coalesce(:active, is_active)
             WHERE code = :code RETURNING id
        """), {"code": code, "name": body.name, "desc": body.description, "active": body.is_active,
               "limits": json.dumps(body.limits) if body.limits is not None else None})).scalar_one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
        await audit(s, admin, "plan.update", target_type="plan", target_id=code,
                    details=body.model_dump(exclude_none=True))
    return {"code": code}


@router.put("/plans/{code}/prices")
async def set_plan_price(code: str, body: PlanPriceIn, admin: Admin = Depends(FINANCE)) -> dict:
    async with admin_session() as s:
        plan_id = (await s.execute(text("SELECT id FROM plans WHERE code = :c"), {"c": code})).scalar_one_or_none()
        if plan_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
        await _upsert_price(s, plan_id, body)
        await audit(s, admin, "plan.price", target_type="plan", target_id=code,
                    details=body.model_dump(mode="json"))
    return {"code": code, "period_months": body.period_months, "price_lyd": money(body.price_lyd)}


# =================================================================== wallet
@router.get("/tenants/{tenant_id}/wallet")
async def get_wallet(tenant_id: UUID, limit: int = Query(50, le=500), before_id: int | None = None,
                     _: Admin = Depends(FINANCE)) -> dict:
    async with admin_session() as s:
        balance = (await s.execute(text("SELECT balance_lyd FROM wallets WHERE tenant_id = :t"),
                                   {"t": tenant_id})).scalar()
        rows = (await s.execute(text("""
            SELECT l.*, r.number AS receipt_number FROM wallet_ledger l
              LEFT JOIN receipts r ON r.ledger_id = l.id
             WHERE l.tenant_id = :t AND (CAST(:before AS bigint) IS NULL OR l.id < CAST(:before AS bigint))
             ORDER BY l.id DESC LIMIT :limit
        """), {"t": tenant_id, "before": before_id, "limit": limit})).mappings().all()
    return {"balance_lyd": money(balance or 0),
            "entries": [{**_entry(r), "note": r["note"], "reference_id": r["reference_id"],
                         "receipt_number": r["receipt_number"], "actor_type": r["actor_type"]} for r in rows]}


@router.post("/tenants/{tenant_id}/wallet/credit", status_code=status.HTTP_201_CREATED)
async def credit_wallet(tenant_id: UUID, body: WalletCredit, idempotency_key: UUID = IdempotencyKey,
                        admin: Admin = Depends(FINANCE)) -> dict:
    """شحن مباشر بعد استلام مبلغ (كاش في المكتب، حوالة تم التحقق منها...). يصدر سند قبض."""
    async with admin_session() as s:
        result = await credit_with_receipt(s, admin, tenant_id, body.amount_lyd, body.method,
                                           idempotency_key, body.reference, body.note)
        await audit(s, admin, "wallet.credit", tenant_id=tenant_id, target_type="ledger",
                    target_id=result["ledger_id"], details={**body.model_dump(mode="json"),
                                                            "idempotency_key": str(idempotency_key)})
    return result


@router.post("/tenants/{tenant_id}/wallet/adjust", status_code=status.HTTP_201_CREATED)
async def adjust_wallet(tenant_id: UUID, body: WalletAdjust, idempotency_key: UUID = IdempotencyKey,
                        admin: Admin = Depends(FINANCE)) -> dict:
    """تسوية (تصحيح خطأ). لا تصدر سند قبض. السبب إلزامي ويظهر في الدفتر والتدقيق."""
    async with admin_session() as s:
        e = (await s.execute(_WALLET_APPLY, {"t": tenant_id, "direction": body.direction,
                                             "amount": body.amount_lyd, "source": "admin_adjustment",
                                             "ref": idempotency_key, "note": body.reason,
                                             "admin": admin.id})).mappings().one()
        await audit(s, admin, "wallet.adjust", tenant_id=tenant_id, target_type="ledger",
                    target_id=e["id"], details=body.model_dump(mode="json"))
    return _entry(e)


# =================================================================== subscriptions
@router.get("/tenants/{tenant_id}/subscription")
async def get_subscription(tenant_id: UUID, _: Admin = Depends(ANY_ADMIN)) -> dict:
    async with admin_session() as s:
        sub = (await s.execute(text("""
            SELECT s.*, pl.code AS plan_code, pl.name AS plan_name,
                   pp.period_months AS renewal_months, pp.price_lyd AS renewal_price_lyd
              FROM subscriptions s JOIN plans pl ON pl.id = s.plan_id
              LEFT JOIN plan_prices pp ON pp.id = s.renewal_price_id
             WHERE s.tenant_id = :t
        """), {"t": tenant_id})).mappings().first()
        if sub is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
        periods = (await s.execute(text("""
            SELECT id, period_start, period_end, amount_lyd, source, created_at
              FROM subscription_periods WHERE tenant_id = :t ORDER BY period_start DESC LIMIT 24
        """), {"t": tenant_id})).mappings().all()
    return {**dict(sub), "renewal_price_lyd": money(sub["renewal_price_lyd"]),
            "periods": [{**dict(p), "amount_lyd": money(p["amount_lyd"])} for p in periods]}


@router.post("/tenants/{tenant_id}/subscription/renew", status_code=status.HTTP_201_CREATED)
async def renew_subscription(tenant_id: UUID, body: SubscriptionRenew,
                             idempotency_key: UUID = IdempotencyKey,
                             admin: Admin = Depends(FINANCE)) -> dict:
    """تجديد من رصيد المحفظة. Idempotency-Key يصبح معرّف الفترة => النقرة المزدوجة لا تخصم مرتين."""
    async with admin_session() as s:
        per = (await s.execute(text("""
            SELECT * FROM subscription_renew(:t, :price, 'admin', :admin, :period_id)
        """), {"t": tenant_id, "price": body.plan_price_id, "admin": admin.id,
               "period_id": idempotency_key})).mappings().one()
        await audit(s, admin, "subscription.renew", tenant_id=tenant_id, target_type="period",
                    target_id=per["id"], details={"plan_price_id": str(body.plan_price_id)})
    return {"period_id": per["id"], "period_start": per["period_start"], "period_end": per["period_end"],
            "amount_lyd": money(per["amount_lyd"])}


@router.post("/tenants/{tenant_id}/subscription/grant", status_code=status.HTTP_201_CREATED)
async def grant_subscription(tenant_id: UUID, body: SubscriptionGrant, admin: Admin = Depends(FINANCE)) -> dict:
    """أيام مجانية (العميل التجريبي، تعويض). بدون خصم، ومسجلة في الفترات والتدقيق."""
    async with admin_session() as s:
        plan_id = None
        if body.plan_code:
            plan_id = (await s.execute(text("SELECT id FROM plans WHERE code = :c"),
                                       {"c": body.plan_code})).scalar_one_or_none()
            if plan_id is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "unknown_plan"})
        per = (await s.execute(text("SELECT * FROM subscription_grant(:t, :plan, :days, :admin)"),
                               {"t": tenant_id, "plan": plan_id, "days": body.days,
                                "admin": admin.id})).mappings().one()
        await audit(s, admin, "subscription.grant", tenant_id=tenant_id, target_type="period",
                    target_id=per["id"], details=body.model_dump())
    return {"period_id": per["id"], "period_end": per["period_end"]}


@router.patch("/tenants/{tenant_id}/subscription")
async def update_subscription(tenant_id: UUID, body: SubscriptionUpdate, admin: Admin = Depends(FINANCE)) -> dict:
    async with admin_session() as s:
        if body.renewal_price_id is not None:
            ok = (await s.execute(text("""
                SELECT 1 FROM plan_prices WHERE id = :p AND is_active
            """), {"p": body.renewal_price_id})).first()
            if ok is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "plan_price_unavailable"})
        row = (await s.execute(text("""
            UPDATE subscriptions
               SET auto_renew = coalesce(:auto, auto_renew),
                   renewal_price_id = coalesce(:price, renewal_price_id),
                   grace_days = coalesce(:grace, grace_days),
                   status = CASE
                              WHEN CAST(:st AS text) IS NULL THEN status
                              -- التفعيل اليدوي مسموح فقط إذا كانت الفترة مدفوعة فعلاً
                              WHEN CAST(:st AS text) = 'active' AND current_period_end > now() THEN 'active'
                              WHEN CAST(:st AS text) = 'active' THEN status
                              ELSE CAST(:st AS text) END
             WHERE tenant_id = :t
            RETURNING status, auto_renew, current_period_end
        """), {"t": tenant_id, "auto": body.auto_renew, "price": body.renewal_price_id,
               "grace": body.grace_days, "st": body.status})).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
        if body.status == "active" and row["status"] != "active":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                {"error": "period_not_paid", "hint": "جدّد الاشتراك أو امنح أياماً"})
        await audit(s, admin, "subscription.update", tenant_id=tenant_id, target_type="subscription",
                    details=body.model_dump(mode="json", exclude_none=True))
    return dict(row)


# =================================================================== manual payments
@router.post("/tenants/{tenant_id}/manual-payments", status_code=status.HTTP_201_CREATED)
async def record_manual_payment(tenant_id: UUID, body: ManualPaymentCreate,
                                admin: Admin = Depends(FINANCE)) -> dict:
    """تسجيل إيصال تحويل أرسله المكتب (واتساب/زيارة) بانتظار المطابقة مع كشف الحساب/الرصيد."""
    async with admin_session() as s:
        mp_id = (await s.execute(text("""
            INSERT INTO manual_payments (tenant_id, method, amount_lyd, sender_name, sender_phone,
                                         bank_reference, proof_url, note, submitted_by)
            VALUES (:t, :method, :amount, :sname, :sphone, :ref, :proof, :note, 'admin') RETURNING id
        """), {"t": tenant_id, "method": body.method, "amount": body.amount_lyd, "sname": body.sender_name,
               "sphone": body.sender_phone, "ref": body.bank_reference, "proof": body.proof_url,
               "note": body.note})).scalar_one()
        await audit(s, admin, "manual_payment.record", tenant_id=tenant_id, target_type="manual_payment",
                    target_id=mp_id, details=body.model_dump(mode="json"))
    return {"id": mp_id, "status": "pending"}


@router.get("/manual-payments")
async def list_manual_payments(status_: str = Query("pending", alias="status"),
                               _: Admin = Depends(FINANCE)) -> list[dict]:
    async with admin_session() as s:
        rows = (await s.execute(text("""
            SELECT m.*, t.name AS tenant_name FROM manual_payments m JOIN tenants t ON t.id = m.tenant_id
             WHERE m.status = :st ORDER BY m.created_at LIMIT 200
        """), {"st": status_})).mappings().all()
    return [{**dict(r), "amount_lyd": money(r["amount_lyd"])} for r in rows]


@router.post("/manual-payments/{payment_id}/approve")
async def approve_manual_payment(payment_id: UUID, admin: Admin = Depends(FINANCE)) -> dict:
    """الموافقة = شحن المحفظة بمرجع الطلب نفسه => الموافقة المكررة لا تشحن مرتين."""
    async with admin_session() as s:
        mp = (await s.execute(text("SELECT * FROM manual_payments WHERE id = :id FOR UPDATE"),
                              {"id": payment_id})).mappings().first()
        if mp is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "payment not found")
        if mp["status"] == "rejected":
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "already_rejected"})
        result = await credit_with_receipt(
            s, admin, mp["tenant_id"], mp["amount_lyd"], mp["method"], mp["id"],
            mp["bank_reference"] or mp["sender_phone"], f"تحويل يدوي ({mp['method']})")
        if mp["status"] == "pending":
            await s.execute(text("""
                UPDATE manual_payments SET status = 'approved', ledger_id = :l, reviewed_by = :admin,
                       reviewed_at = now() WHERE id = :id
            """), {"id": payment_id, "l": result["ledger_id"], "admin": admin.id})
            await audit(s, admin, "manual_payment.approve", tenant_id=mp["tenant_id"],
                        target_type="manual_payment", target_id=payment_id,
                        details={"amount_lyd": money(mp["amount_lyd"]), "receipt": result["receipt_number"]})
    return result


@router.post("/manual-payments/{payment_id}/reject")
async def reject_manual_payment(payment_id: UUID, body: ManualPaymentReject,
                                admin: Admin = Depends(FINANCE)) -> dict:
    async with admin_session() as s:
        row = (await s.execute(text("""
            UPDATE manual_payments SET status = 'rejected', rejection_reason = :r, reviewed_by = :admin,
                   reviewed_at = now()
             WHERE id = :id AND status = 'pending' RETURNING tenant_id
        """), {"id": payment_id, "r": body.reason, "admin": admin.id})).scalar_one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "not_pending"})
        await audit(s, admin, "manual_payment.reject", tenant_id=row, target_type="manual_payment",
                    target_id=payment_id, details={"reason": body.reason})
    return {"id": payment_id, "status": "rejected"}


# =================================================================== receipts
@router.get("/tenants/{tenant_id}/receipts")
async def list_receipts(tenant_id: UUID, _: Admin = Depends(ANY_ADMIN)) -> list[dict]:
    async with admin_session() as s:
        rows = (await s.execute(text("""
            SELECT id, number, amount_lyd, method, reference, issued_at FROM receipts
             WHERE tenant_id = :t ORDER BY issued_at DESC LIMIT 200
        """), {"t": tenant_id})).mappings().all()
    return [{**dict(r), "amount_lyd": money(r["amount_lyd"])} for r in rows]


@router.get("/receipts/{receipt_id}")
async def get_receipt(receipt_id: UUID, _: Admin = Depends(ANY_ADMIN)) -> dict:
    """بيانات سند القبض (توليد PDF في المرحلة 7)."""
    async with admin_session() as s:
        r = (await s.execute(text("""
            SELECT r.*, t.name AS tenant_name, t.slug AS tenant_slug, l.balance_after, l.note
              FROM receipts r JOIN tenants t ON t.id = r.tenant_id JOIN wallet_ledger l ON l.id = r.ledger_id
             WHERE r.id = :id
        """), {"id": receipt_id})).mappings().first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "receipt not found")
    return {**dict(r), "amount_lyd": money(r["amount_lyd"]), "balance_after": money(r["balance_after"]),
            "currency": "LYD"}
