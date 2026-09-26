"""Admin authentication: opaque API tokens (hash stored), role-based access, audit trail.

الأدوار:
  superadmin : كل شيء
  support    : الوكالات، القنوات، الموظفون
  finance    : المحافظ، الاشتراكات، القسائم، التحويلات
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text

from app.db.admin import admin_session

TOKEN_PREFIX = "adm_"


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Admin:
    id: UUID
    email: str
    role: str
    ip: str | None


_LOOKUP = text("""
    UPDATE admin_tokens t SET last_used_at = now()
      FROM admin_users u
     WHERE t.token_hash = :h AND t.revoked_at IS NULL
       AND (t.expires_at IS NULL OR t.expires_at > now())
       AND u.id = t.admin_id AND u.is_active
    RETURNING u.id, u.email, u.role
""")


async def current_admin(request: Request) -> Admin:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer " + TOKEN_PREFIX):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing admin token")
    async with admin_session() as s:
        row = (await s.execute(_LOOKUP, {"h": hash_token(header[7:].strip())})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin token")
    return Admin(row["id"], row["email"], row["role"], request.client.host if request.client else None)


def require(*roles: str):
    allowed = set(roles) | {"superadmin"}

    async def _dep(admin: Admin = Depends(current_admin)) -> Admin:
        if admin.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role: {', '.join(sorted(allowed))}")
        return admin
    return _dep


SUPPORT = require("support")
FINANCE = require("finance")
ANY_ADMIN = require("support", "finance")

_AUDIT = text("""
    INSERT INTO admin_audit_log (admin_id, action, target_tenant_id, target_type, target_id, details, ip)
    VALUES (:admin_id, :action, :tenant_id, :target_type, :target_id, CAST(:details AS jsonb),
            CAST(:ip AS inet))
""")


async def audit(session: Any, admin: Admin, action: str, *, tenant_id: UUID | None = None,
                target_type: str | None = None, target_id: Any = None,
                details: dict[str, Any] | None = None) -> None:
    """يُكتب داخل نفس transaction العملية: إذا فشلت العملية لا يبقى سجل مضلل."""
    await session.execute(_AUDIT, {
        "admin_id": admin.id, "action": action, "tenant_id": tenant_id,
        "target_type": target_type, "target_id": str(target_id) if target_id is not None else None,
        "details": json.dumps(details or {}, ensure_ascii=False, default=str), "ip": admin.ip,
    })
