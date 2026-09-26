"""Request authentication and tenant context for the public API (/api/v1).

- الجلسة من هيدر `Authorization: Bearer ses_...` (الجوال) أو كوكي httpOnly (الويب).
- CSRF: الطلبات المغيِّرة المعتمدة على الكوكي يجب أن تأتي من Origin مسموح.
- الوكالة: هيدر `X-Tenant-ID`، أو الوكالة الوحيدة للمستخدم. العضوية تُتحقق من قاعدة البيانات
  في كل طلب، ثم تُفتح tenant_session => RLS هي خط الدفاع الأخير.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.db.tenant import system_session, user_session
from app.identity import queries as iq
from app.identity.security import token_hash

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ROLE_RANK = {"agent": 1, "admin": 2, "owner": 3}


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str
    full_name: str
    phone_e164: str | None
    session_id: UUID


@dataclass(frozen=True)
class TenantContext:
    user: CurrentUser
    tenant_id: UUID
    role: str

    def at_least(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _session_token(request: Request) -> tuple[str | None, bool]:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer ses_"):
        return header[7:].strip(), False
    cookie = request.cookies.get(get_settings().session_cookie_name)
    return (cookie, True) if cookie else (None, False)


def _check_origin(request: Request) -> None:
    allowed = get_settings().web_allowed_origins
    origin = request.headers.get("Origin")
    if not origin or origin not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"error": "origin_not_allowed"})


async def current_user(request: Request) -> CurrentUser:
    token, via_cookie = _session_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, {"error": "not_authenticated"})
    if via_cookie and request.method not in SAFE_METHODS:
        _check_origin(request)
    async with system_session() as s:
        row = (await s.execute(iq.RESOLVE_SESSION, {"h": token_hash(token)})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, {"error": "session_expired"})
    return CurrentUser(row["id"], row["email"], row["full_name"], row["phone_e164"], row["session_id"])


async def tenant_context(request: Request, user: CurrentUser = Depends(current_user)) -> TenantContext:
    raw = request.headers.get("X-Tenant-ID")
    if not raw and request.method in SAFE_METHODS:
        # EventSource (SSE) في المتصفح لا يرسل هيدرات مخصصة. للقراءة فقط؛ العضوية تُفحص بنفس الطريقة.
        raw = request.query_params.get("tenant")
    async with user_session(user.id) as s:
        if raw:
            try:
                tenant_id = UUID(raw)
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, {"error": "invalid_tenant_id"}) from None
            role = (await s.execute(iq.MEMBERSHIP, {"tenant_id": tenant_id})).scalar_one_or_none()
        else:
            tenants = (await s.execute(iq.MY_TENANTS)).mappings().all()
            if len(tenants) != 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, {"error": "tenant_required",
                                                                  "hint": "send X-Tenant-ID"})
            tenant_id, role = tenants[0]["tenant_id"], tenants[0]["role"]
    if role is None:
        # نفس الرد لوكالة غير موجودة أو لا ينتمي لها => لا نكشف وجود الوكالات
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"error": "tenant_not_found"})
    return TenantContext(user, tenant_id, role)


def require_role(minimum: str):
    async def _dep(ctx: TenantContext = Depends(tenant_context)) -> TenantContext:
        if not ctx.at_least(minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, {"error": "insufficient_role", "required": minimum})
        return ctx
    return _dep


MEMBER = require_role("agent")
ADMIN = require_role("admin")
OWNER = require_role("owner")
