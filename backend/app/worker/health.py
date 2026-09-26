"""Periodic channel token health check (Token lifecycle monitoring).

كل دورة: channels_due_for_health_check() تحجز القنوات التي لم تُفحص منذ N ساعات (عبر كل الوكالات،
معرّفات فقط) => لكل قناة: قراءة التوكن داخل سياق وكالتها => طلب خفيف لـ Meta => تحديث الحالة.
  - خطأ 190 أو debug_token.is_valid=false  => needs_reauth (يظهر للمكتب زر إعادة الربط)
  - توكن ينتهي خلال 7 أيام                 => تحذير في last_error مع بقاء القناة نشطة
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.channels.meta_graph import MetaGraphClient, MetaGraphError
from app.core.config import Settings
from app.core.crypto import TokenDecryptionError, decrypt_token
from app.db.tenant import system_session, tenant_session
from app.identity import queries as iq

log = logging.getLogger(__name__)

CLAIM = text("""
    SELECT channel_account_id, tenant_id, channel
      FROM channels_due_for_health_check(:limit, make_interval(hours => :hours))
""")
EXPIRY_WARNING_SECONDS = 7 * 24 * 3600


async def check_one(graph: MetaGraphClient, settings: Settings, channel: str, external_id: str,
                    token_enc: bytes | None, config: dict[str, Any]) -> tuple[str, str | None]:
    """يعيد (status, error). منفصلة عن قاعدة البيانات لتسهيل الاختبار."""
    try:
        token = decrypt_token(token_enc)
    except TokenDecryptionError as exc:
        return "needs_reauth", str(exc)
    try:
        if channel == "whatsapp":
            await graph.get_phone_number(external_id, token)
        else:
            await graph.get_page(config.get("page_id") or external_id, token)
        if settings.meta_app_id:
            app_token = f"{settings.meta_app_id}|{settings.meta_app_secret.get_secret_value()}"
            info = await graph.debug_token(token, app_token)
            if info.get("is_valid") is False:
                return "needs_reauth", "token invalid (debug_token)"
            expires = int(info.get("expires_at") or 0)     # 0 = لا ينتهي
            if expires and expires - time.time() < EXPIRY_WARNING_SECONDS:
                return "active", f"token expires soon ({expires})"
    except MetaGraphError as exc:
        if exc.code == 190:
            return "needs_reauth", f"190: {exc.args[0]}"[:500]
        return "active", f"check failed: {exc.code}: {exc.args[0]}"[:500]   # خطأ مؤقت: لا نفصل القناة
    return "active", None


async def run_channel_health_tick(graph: MetaGraphClient, settings: Settings, limit: int = 20) -> int:
    async with system_session() as s:
        due = (await s.execute(CLAIM, {"limit": limit, "hours": settings.channel_health_every_hours})).mappings().all()
    for row in due:
        tenant_id: UUID = row["tenant_id"]
        try:
            async with tenant_session(tenant_id) as s:
                ch = (await s.execute(iq.CHANNEL_FOR_UPDATE, {"id": row["channel_account_id"]})).mappings().first()
            if ch is None or ch["status"] != "active":
                continue
            config = ch["config"] if isinstance(ch["config"], dict) else json.loads(ch["config"] or "{}")
            status, error = await check_one(graph, settings, ch["channel"], ch["external_id"],
                                            ch["access_token_enc"], config)
            async with tenant_session(tenant_id) as s:
                await s.execute(iq.SET_CHANNEL_STATUS, {"id": ch["id"], "status": status, "error": error})
            if status != "active":
                log.warning("health: channel %s (%s) -> %s: %s", ch["id"], ch["channel"], status, error)
        except Exception:  # noqa: BLE001 — قناة واحدة لا توقف الدورة
            log.exception("health: channel %s check crashed", row["channel_account_id"])
    return len(due)
