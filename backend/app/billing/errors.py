"""Map database money errors (custom SQLSTATE BLxxx from migration 0008) to API errors."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

_MAP: dict[str, tuple[int, str]] = {
    "BL001": (status.HTTP_409_CONFLICT, "insufficient_funds"),
    "BL002": (status.HTTP_409_CONFLICT, "idempotency_conflict"),
    "BL003": (status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_amount"),
    "BL004": (status.HTTP_404_NOT_FOUND, "subscription_not_found"),
    "BL005": (status.HTTP_409_CONFLICT, "plan_price_unavailable"),
}


def sqlstate_of(exc: BaseException) -> str | None:
    """يعمل مع SQLAlchemy + asyncpg أو psycopg: نبحث في السلسلة عن pgcode/sqlstate."""
    seen: set[int] = set()
    cur: Any = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        for attr in ("sqlstate", "pgcode"):
            code = getattr(cur, attr, None)
            if isinstance(code, str) and len(code) == 5:
                return code
        cur = getattr(cur, "orig", None) or cur.__cause__
    return None


def to_http(exc: BaseException) -> HTTPException | None:
    code = sqlstate_of(exc)
    if code in _MAP:
        http, name = _MAP[code]
        return HTTPException(http, {"error": name, "detail": str(getattr(exc, "orig", exc))[:300]})
    if code == "23505":
        return HTTPException(status.HTTP_409_CONFLICT, {"error": "already_exists"})
    if code == "23503":
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "invalid_reference"})
    if code == "23514":
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "constraint_violation"})
    return None
