"""Catalog: brochure import (-> draft packages) + review / publish (المرحلة 7a).

- الرفع: جسم الطلب هو الملف نفسه (بدون multipart)، والنوع يُتحقق منه بالبايتات الأولى.
- المسودات لا يراها البوت. publish = اعتماد بشري للبرنامج وأسعاره.
- التصحيح قبل الاعتماد فقط (سعر، موعد، حذف المسودة). التحرير الكامل للبرامج المعتمدة: Agent Studio.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.ai import queries as aq
from app.ai.catalog import UploadError, safe_filename, validate_upload
from app.api.deps import ADMIN, MEMBER, TenantContext
from app.core.config import get_settings
from app.db.tenant import tenant_session

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])

PackageStatus = Literal["draft", "active", "full", "archived"]

UPLOAD_ERRORS: dict[str, tuple[int, str]] = {
    "empty_file": (status.HTTP_422_UNPROCESSABLE_ENTITY, "الملف فارغ"),
    "file_too_large": (status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "الملف كبير؛ الحد الأقصى 10 ميغابايت"),
    "unsupported_file_type": (status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "ارفع صورة (JPG أو PNG أو WEBP) أو ملف PDF"),
    "content_type_mismatch": (status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "نوع الملف لا يطابق محتواه"),
}


def _upload_error(code: str) -> HTTPException:
    http_status, message = UPLOAD_ERRORS[code]
    return HTTPException(http_status, {"error": code, "message": message})


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, {"error": f"{what}_not_found"})


def _not_draft() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, {
        "error": "package_not_draft", "message": "التعديل هنا للمسودات فقط قبل الاعتماد"})


async def _read_body(request: Request, max_bytes: int) -> bytes:
    """يقرأ الجسم بحد أقصى دون تحميل ملف ضخم كاملاً في الذاكرة."""
    if int(request.headers.get("content-length") or 0) > max_bytes:
        raise _upload_error("file_too_large")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > max_bytes:
            raise _upload_error("file_too_large")
    return bytes(buf)


# ------------------------------------------------------------------ imports
@router.post("/imports", status_code=status.HTTP_202_ACCEPTED)
async def create_import(request: Request, response: Response,
                        filename: str | None = Query(None, max_length=200),
                        ctx: TenantContext = Depends(ADMIN)) -> dict:
    """جسم الطلب = الصورة أو PDF. نفس الملف مرتين => نفس الطلب (duplicate) بدون معالجة ثانية."""
    max_bytes = get_settings().catalog_import_max_bytes
    data = await _read_body(request, max_bytes)
    try:
        mime = validate_upload(data, request.headers.get("content-type"), max_bytes)
    except UploadError as exc:
        raise _upload_error(str(exc)) from None
    sha = hashlib.sha256(data).hexdigest()
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        existing = (await s.execute(aq.FIND_IMPORT_BY_SHA, {"sha256": sha})).mappings().first()
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return {"id": existing["id"], "status": existing["status"], "duplicate": True}
        row = (await s.execute(aq.INSERT_CATALOG_IMPORT, {
            "filename": safe_filename(filename), "mime_type": mime, "size_bytes": len(data),
            "sha256": sha, "file_data": data})).mappings().one()
        await s.execute(aq.ENQUEUE_CATALOG_EXTRACTION, {"catalog_import_id": row["id"]})
    return {"id": row["id"], "status": row["status"], "duplicate": False}


def _import_out(r: Any) -> dict:
    return {**dict(r), "warnings": _json(r["warnings"])}


@router.get("/imports")
async def list_imports(ctx: TenantContext = Depends(ADMIN)) -> list[dict]:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        return [_import_out(r) for r in (await s.execute(aq.LIST_IMPORTS)).mappings().all()]


@router.get("/imports/{import_id}")
async def get_import(import_id: UUID, ctx: TenantContext = Depends(ADMIN)) -> dict:
    """حالة المعالجة + المسودات الناتجة (الواجهة تستطلعها حتى done / failed)."""
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        row = (await s.execute(aq.IMPORT_DETAIL, {"id": import_id})).mappings().first()
        if row is None:
            raise _not_found("import")
        packages = [dict(p) for p in (await s.execute(aq.LIST_PACKAGES, {
            "status": None, "import_id": import_id})).mappings().all()]
    return {**_import_out(row), "packages": packages}


# ------------------------------------------------------------------ packages (review)
@router.get("/packages")
async def list_packages(status_: PackageStatus | None = Query(None, alias="status"),
                        import_id: UUID | None = None, ctx: TenantContext = Depends(MEMBER)) -> list[dict]:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        return [dict(r) for r in (await s.execute(aq.LIST_PACKAGES, {
            "status": status_, "import_id": import_id})).mappings().all()]


@router.get("/packages/{package_id}")
async def get_package(package_id: UUID, ctx: TenantContext = Depends(MEMBER)) -> dict:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        row = (await s.execute(aq.PACKAGE_DETAIL, {"id": package_id})).mappings().first()
        if row is None:
            raise _not_found("package")
        p = {"id": package_id}
        aliases = [dict(r) for r in (await s.execute(aq.PACKAGE_ALIASES, p)).mappings().all()]
        hotels = [dict(r) for r in (await s.execute(aq.PACKAGE_HOTELS, p)).mappings().all()]
        departures = [dict(r) for r in (await s.execute(aq.PACKAGE_DEPARTURES, p)).mappings().all()]
        prices = [dict(r) for r in (await s.execute(aq.PACKAGE_PRICES, p)).mappings().all()]
    return {**dict(row), "includes": _json(row["includes"]), "excludes": _json(row["excludes"]),
            "aliases": aliases, "hotels": hotels, "departures": departures, "prices": prices}


@router.post("/packages/{package_id}/publish")
async def publish_package(package_id: UUID, ctx: TenantContext = Depends(ADMIN)) -> dict:
    """اعتماد المسودة: يراها البوت ويبحث فيها، وأسعارها تُعدّ مؤكدة من الآن."""
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        p = (await s.execute(aq.PACKAGE_FOR_PUBLISH, {"id": package_id})).mappings().first()
        if p is None:
            raise _not_found("package")
        if p["status"] != "draft":
            raise HTTPException(status.HTTP_409_CONFLICT, {"error": "package_not_draft",
                                                           "message": "البرنامج معتمد من قبل"})
        if p["adult_prices"] == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
                "error": "package_has_no_prices", "message": "أضف سعراً للبالغين قبل الاعتماد"})
        await s.execute(aq.PUBLISH_PACKAGE, {"id": package_id})
        await s.execute(aq.CONFIRM_PACKAGE_PRICES, {"id": package_id})
    return {"id": package_id, "status": "active"}


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_draft(package_id: UUID, ctx: TenantContext = Depends(ADMIN)) -> Response:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        p = (await s.execute(aq.PACKAGE_FOR_PUBLISH, {"id": package_id})).mappings().first()
        if p is None:
            raise _not_found("package")
        if p["status"] != "draft":
            raise _not_draft()
        await s.execute(aq.DELETE_DRAFT_PACKAGE, {"id": package_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ corrections before publishing
class PriceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal | None = Field(None, ge=0, lt=1_000_000, max_digits=12, decimal_places=2)
    notes: str | None = Field(None, max_length=200)


@router.patch("/prices/{price_id}")
async def update_price(price_id: UUID, body: PriceUpdate, ctx: TenantContext = Depends(ADMIN)) -> dict:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        p = (await s.execute(aq.PRICE_FOR_UPDATE, {"id": price_id})).mappings().first()
        if p is None:
            raise _not_found("price")
        if p["package_status"] != "draft":
            raise _not_draft()
        row = (await s.execute(aq.UPDATE_PRICE, {
            "id": price_id, "amount": str(body.amount) if body.amount is not None else None,
            "set_notes": "notes" in body.model_fields_set,
            "notes": (body.notes or "").strip() or None})).mappings().one()
    return dict(row)


@router.delete("/prices/{price_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_price(price_id: UUID, ctx: TenantContext = Depends(ADMIN)) -> Response:
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        p = (await s.execute(aq.PRICE_FOR_UPDATE, {"id": price_id})).mappings().first()
        if p is None:
            raise _not_found("price")
        if p["package_status"] != "draft":
            raise _not_draft()
        await s.execute(aq.DELETE_PRICE, {"id": price_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/departures/{departure_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_departure(departure_id: UUID, ctx: TenantContext = Depends(ADMIN)) -> Response:
    """موعد مستخرج خطأ: يُحذف مع أسعاره الخاصة به."""
    async with tenant_session(ctx.tenant_id, ctx.user.id) as s:
        d = (await s.execute(aq.DEPARTURE_FOR_UPDATE, {"id": departure_id})).mappings().first()
        if d is None:
            raise _not_found("departure")
        if d["package_status"] != "draft":
            raise _not_draft()
        await s.execute(aq.DELETE_DEPARTURE, {"id": departure_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
