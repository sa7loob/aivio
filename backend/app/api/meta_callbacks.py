"""Meta compliance callbacks (required for App Review).

  POST /meta/deauthorize          : المستخدم أزال التطبيق من حسابه في فيسبوك
  POST /meta/data-deletion        : المستخدم طلب حذف بياناته => {url, confirmation_code}
  GET  /meta/data-deletion/{code} : صفحة حالة الطلب (الرابط الذي تعرضه Meta للمستخدم)

الطلب يحمل signed_request (form-encoded) موقّعاً بسر التطبيق؛ أي توقيع خاطئ => 400.
"""
from __future__ import annotations

import html
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.channels.meta_signature import parse_signed_request
from app.core.config import get_settings
from app.db.tenant import system_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/meta", tags=["meta-compliance"])

_REVOKE = text("SELECT confirmation_code, channels_affected FROM meta_user_revoked(:uid, :kind)")
_STATUS = text("SELECT status, created_at, completed_at FROM meta_deletion_status(:code)")


async def _meta_user_id(request: Request) -> str:
    raw = (await request.body())[:10_000].decode("utf-8", "replace")
    signed = (parse_qs(raw).get("signed_request") or [None])[0]
    payload = parse_signed_request(signed, get_settings().meta_app_secret.get_secret_value())
    if payload is None or not payload.get("user_id"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"error": "invalid_signed_request"})
    return str(payload["user_id"])


@router.post("/deauthorize")
async def deauthorize(request: Request) -> dict:
    uid = await _meta_user_id(request)
    async with system_session() as s:
        row = (await s.execute(_REVOKE, {"uid": uid, "kind": "deauthorize"})).mappings().one()
    log.info("meta: deauthorize user=%s channels=%s", uid, row["channels_affected"])
    return {"status": "ok"}


@router.post("/data-deletion")
async def data_deletion(request: Request) -> dict:
    uid = await _meta_user_id(request)
    async with system_session() as s:
        row = (await s.execute(_REVOKE, {"uid": uid, "kind": "data_deletion"})).mappings().one()
    code = row["confirmation_code"]
    log.info("meta: data deletion user=%s channels=%s code=%s", uid, row["channels_affected"], code)
    base = get_settings().public_base_url.rstrip("/")
    return {"url": f"{base}/meta/data-deletion/{code}", "confirmation_code": code}


@router.get("/data-deletion/{code}", response_class=HTMLResponse)
async def deletion_status(code: str) -> HTMLResponse:
    if not code.isalnum() or len(code) > 64:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    async with system_session() as s:
        row = (await s.execute(_STATUS, {"code": code})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    done = row["status"] == "completed"
    body = f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>حالة حذف البيانات</title>
<body style="font-family:system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;line-height:1.7">
<h1 style="font-size:1.3rem">حالة طلب حذف البيانات</h1>
<p>رمز التأكيد: <bdi>{html.escape(code)}</bdi></p>
<p>{"تم حذف بيانات ربط حسابك في فيسبوك (التوكنات والارتباط بالقنوات)." if done else "الطلب قيد التنفيذ."}</p>
<p style="color:#667085">Data deletion status: {"completed" if done else "in progress"} ({row["created_at"]:%Y-%m-%d})</p>
</body></html>"""
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})
