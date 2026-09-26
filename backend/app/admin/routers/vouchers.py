"""Platform vouchers: issue batches for distributors, void leaked batches, redeem on behalf of an office."""
from __future__ import annotations

import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import text

from app.admin.auth import FINANCE, Admin, audit
from app.admin.routers.billing import _RECEIPT, _AUTORENEW
from app.admin.schemas import VoucherBatchCreate, VoucherBatchVoid, VoucherRedeem, money
from app.billing.vouchers import format_code, generate_code, hash_code, normalize_code
from app.core.config import get_settings
from app.db.admin import admin_session

router = APIRouter(prefix="/admin", tags=["vouchers"])


def _pepper() -> str:
    pepper = get_settings().voucher_pepper
    if pepper is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "VOUCHER_PEPPER is not configured")
    return pepper.get_secret_value()


@router.post("/voucher-batches", status_code=status.HTTP_201_CREATED,
             responses={201: {"content": {"text/csv": {}}}})
async def create_batch(body: VoucherBatchCreate, admin: Admin = Depends(FINANCE)) -> Response:
    """يولّد الرموز ويعيدها كملف CSV مرة واحدة فقط. قاعدة البيانات تحفظ الـ hash وآخر 4 أرقام."""
    pepper = _pepper()
    codes: set[str] = set()
    while len(codes) < body.quantity:
        codes.add(generate_code())
    ordered = sorted(codes)

    async with admin_session() as s:
        batch_id = (await s.execute(text("""
            INSERT INTO voucher_batches (distributor_name, denomination_lyd, quantity, expires_at, created_by)
            VALUES (:d, :amount, :qty, :exp, :admin) RETURNING id
        """), {"d": body.distributor_name, "amount": body.denomination_lyd, "qty": body.quantity,
               "exp": body.expires_at, "admin": admin.id})).scalar_one()
        await s.execute(text("""
            INSERT INTO vouchers (batch_id, code_hash, last4, amount_lyd)
            SELECT :b, h, l, :amount
              FROM unnest(CAST(:hashes AS text[]), CAST(:last4s AS text[])) AS x(h, l)
        """), {"b": batch_id, "amount": body.denomination_lyd,
               "hashes": [hash_code(c, pepper) for c in ordered], "last4s": [c[-4:] for c in ordered]})
        await audit(s, admin, "voucher_batch.create", target_type="voucher_batch", target_id=batch_id,
                    details={"distributor": body.distributor_name, "quantity": body.quantity,
                             "denomination_lyd": money(body.denomination_lyd)})

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["code", "amount_lyd", "expires_at", "batch_id"])
    exp = body.expires_at.date().isoformat() if body.expires_at else ""
    for c in ordered:
        w.writerow([format_code(c), money(body.denomination_lyd), exp, str(batch_id)])
    return Response(content=buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    status_code=status.HTTP_201_CREATED,
                    headers={"Content-Disposition": f'attachment; filename="vouchers-{batch_id}.csv"',
                             "Cache-Control": "no-store"})


@router.get("/voucher-batches")
async def list_batches(_: Admin = Depends(FINANCE)) -> list[dict]:
    async with admin_session() as s:
        rows = (await s.execute(text("""
            SELECT b.id, b.distributor_name, b.denomination_lyd, b.quantity, b.status, b.expires_at,
                   b.created_at,
                   count(*) FILTER (WHERE v.status = 'active')   AS active,
                   count(*) FILTER (WHERE v.status = 'redeemed') AS redeemed,
                   count(*) FILTER (WHERE v.status = 'void')     AS void,
                   coalesce(sum(v.amount_lyd) FILTER (WHERE v.status = 'redeemed'), 0) AS redeemed_lyd
              FROM voucher_batches b LEFT JOIN vouchers v ON v.batch_id = b.id
             GROUP BY b.id ORDER BY b.created_at DESC
        """))).mappings().all()
    return [{**dict(r), "denomination_lyd": money(r["denomination_lyd"]),
             "redeemed_lyd": money(r["redeemed_lyd"])} for r in rows]


@router.post("/voucher-batches/{batch_id}/void")
async def void_batch(batch_id: UUID, body: VoucherBatchVoid, admin: Admin = Depends(FINANCE)) -> dict:
    """عند تسريب ملف الرموز: القسائم غير المستخدمة تُلغى فوراً؛ المستخدمة تبقى صحيحة."""
    async with admin_session() as s:
        ok = (await s.execute(text("""
            UPDATE voucher_batches SET status = 'void', void_reason = :r, voided_at = now()
             WHERE id = :b AND status = 'active' RETURNING id
        """), {"b": batch_id, "r": body.reason})).scalar_one_or_none()
        if ok is None:
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "not_active_or_missing"})
        voided = (await s.execute(text("""
            UPDATE vouchers SET status = 'void' WHERE batch_id = :b AND status = 'active'
        """), {"b": batch_id})).rowcount
        await audit(s, admin, "voucher_batch.void", target_type="voucher_batch", target_id=batch_id,
                    details={"reason": body.reason, "voided": voided})
    return {"batch_id": batch_id, "voided": voided}


@router.post("/tenants/{tenant_id}/vouchers/redeem")
async def redeem_for_tenant(tenant_id: UUID, body: VoucherRedeem, request: Request,
                            admin: Admin = Depends(FINANCE)) -> dict:
    """المكتب يرسل رمز القسيمة للدعم => الشحن نيابة عنه (نفس الدالة التي ستستخدمها لوحة الوكالة)."""
    code = normalize_code(body.code)
    if code is None:   # خطأ كتابة: لا يُحسب محاولة فاشلة
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "malformed_code"})
    async with admin_session(tenant_id) as s:
        r = (await s.execute(text("""
            SELECT * FROM redeem_voucher(:h, CAST(:ip AS inet), 'admin', :admin)
        """), {"h": hash_code(code, _pepper()), "ip": admin.ip, "admin": admin.id})).mappings().one()
        if not r["ok"]:
            await audit(s, admin, "voucher.redeem_failed", tenant_id=tenant_id, target_type="voucher",
                        details={"reason": r["reason"], "last4": code[-4:]})
            result = {"ok": False, "reason": r["reason"]}
        else:
            receipt = (await s.execute(_RECEIPT, {"ledger_id": r["ledger_id"], "reference": f"****{code[-4:]}",
                                                  "admin": admin.id})).mappings().one()
            renewed = (await s.execute(_AUTORENEW, {"t": tenant_id})).scalar_one()
            await audit(s, admin, "voucher.redeem", tenant_id=tenant_id, target_type="voucher",
                        target_id=r["voucher_id"], details={"amount_lyd": money(r["amount_lyd"])})
            result = {"ok": True, "amount_lyd": money(r["amount_lyd"]), "balance_lyd": money(r["balance_lyd"]),
                      "receipt_number": receipt["number"], "auto_renewed": renewed,
                      "already_redeemed": r["reason"] == "already_redeemed_by_you"}
    if not result["ok"]:
        # سجل المحاولة الفاشلة محفوظ (commit) قبل رفع الخطأ
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS if result["reason"] == "too_many_attempts"
                            else status.HTTP_422_UNPROCESSABLE_ENTITY, result)
    return result
