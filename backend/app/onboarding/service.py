"""Self-serve channel onboarding (WhatsApp Embedded Signup, Facebook Login for Pages + linked Instagram).

آلة حالات قابلة للاستئناف (onboarding_sessions):
    started --(exchange code)--> token_exchanged --(subscribe/register/save)--> completed
                         \\-> failed (قبل الحصول على توكن: يبدأ العميل من جديد)
    token_exchanged / failed-with-token --(retry)--> completed

قواعد:
  - الـ code صالح ~30 ثانية: يُستبدل أولاً، ويُحفظ التوكن مشفّراً قبل أي خطوة أخرى.
  - لا transaction مفتوحة أثناء طلبات Meta (قراءة => HTTP => كتابة).
  - state (مرة واحدة، مرتبط بالوكالة والجلسة) يحمي من CSRF وخلط الجلسات.
  - قناة مملوكة لوكالة أخرى => رفض (upsert_channel_account يعيد conflict بدون كشف أي تفاصيل).
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.agent.types import SessionFactory
from app.channels.meta_graph import MetaGraphClient, MetaGraphError
from app.core.config import Settings
from app.core.crypto import decrypt_token, encrypt_token
from app.identity import queries as iq
from app.identity.security import new_token, token_hash

log = logging.getLogger(__name__)

FLOW_WHATSAPP = "whatsapp_embedded_signup"
FLOW_FACEBOOK = "facebook_login"
TOKEN_RETRY_MINUTES = 24 * 60          # بعد استلام التوكن: مهلة 24 ساعة لإعادة محاولة الخطوات


def _public(meta: dict[str, Any] | None) -> dict[str, Any]:
    """ما يُعاد للواجهة: بدون أسرار أو معرّفات Meta الداخلية."""
    return {k: v for k, v in (meta or {}).items() if k not in ("pin_enc", "meta_user_id")}


class OnboardingError(Exception):
    def __init__(self, http_status: int, code: str, message: str | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.http_status, self.code, self.message, self.details = http_status, code, message, details or {}


@dataclass(frozen=True)
class Actor:
    tenant_id: UUID
    user_id: UUID


class OnboardingService:
    def __init__(self, graph: MetaGraphClient, settings: Settings, session_factory: SessionFactory) -> None:
        self.graph = graph
        self.settings = settings
        self._session = session_factory

    # ------------------------------------------------------------------ helpers
    def _db(self, actor: Actor):
        return self._session(actor.tenant_id, actor.user_id)

    def _app_credentials(self) -> tuple[str, str]:
        if not self.settings.meta_app_id:
            raise OnboardingError(503, "meta_app_not_configured")
        return self.settings.meta_app_id, self.settings.meta_app_secret.get_secret_value()

    async def _update(self, actor: Actor, session_id: UUID, *, status: str, step: str | None,
                      error: str | None = None, meta: dict[str, Any] | None = None,
                      token_enc: bytes | None = None, clear_token: bool = False,
                      extend_minutes: int | None = None) -> None:
        async with self._db(actor) as s:
            await s.execute(iq.UPDATE_ONBOARDING, {
                "id": session_id, "status": status, "step": step, "error": error,
                "meta": json.dumps(meta or {}, ensure_ascii=False), "token_enc": token_enc,
                "clear_token": clear_token, "extend_minutes": extend_minutes})

    async def _load(self, actor: Actor, session_id: UUID, flow: str, state: str | None,
                    *, reserve: bool = False) -> dict[str, Any]:
        """يتحقق من الجلسة و state. reserve=True: يحجز الجلسة لاستبدال الـ code (step=exchanging)
        داخل نفس القفل => طلبان متزامنان لا يستبدلان نفس الـ code مرتين."""
        async with self._db(actor) as s:
            row = (await s.execute(iq.ONBOARDING_FOR_UPDATE, {"id": session_id})).mappings().first()
            if row is None or row["flow"] != flow:
                raise OnboardingError(404, "onboarding_session_not_found")
            if state is not None and not hmac.compare_digest(row["state_hash"], token_hash(state)):
                raise OnboardingError(403, "state_mismatch")
            row = dict(row)
            if reserve and row["status"] == "started":
                if row["expired"]:
                    raise OnboardingError(409, "onboarding_session_expired", "ابدأ الربط من جديد")
                if row["step"] == "exchanging":
                    raise OnboardingError(409, "onboarding_in_progress")
                await s.execute(iq.UPDATE_ONBOARDING, {
                    "id": session_id, "status": "started", "step": "exchanging", "error": None,
                    "meta": "{}", "token_enc": None, "clear_token": False, "extend_minutes": None})
            return row

    # ------------------------------------------------------------------ start
    async def start(self, actor: Actor, flow: str) -> dict[str, Any]:
        app_id, _ = self._app_credentials()
        config_id = (self.settings.meta_es_config_id if flow == FLOW_WHATSAPP
                     else self.settings.meta_fb_login_config_id)
        if not config_id:
            raise OnboardingError(503, "meta_login_config_not_configured")
        state = new_token("st")
        async with self._db(actor) as s:
            row = (await s.execute(iq.CREATE_ONBOARDING, {
                "flow": flow, "state_hash": token_hash(state),
                "minutes": self.settings.onboarding_session_minutes})).mappings().one()
        return {"session_id": row["id"], "state": state, "expires_at": row["expires_at"],
                "app_id": app_id, "config_id": config_id,
                "graph_api_version": self.settings.meta_graph_api_version}

    async def status(self, actor: Actor, session_id: UUID) -> dict[str, Any]:
        async with self._db(actor) as s:
            row = (await s.execute(iq.GET_ONBOARDING, {"id": session_id})).mappings().first()
        if row is None:
            raise OnboardingError(404, "onboarding_session_not_found")
        return {**dict(row), "meta": _public(row["meta"])}

    async def _exchange(self, actor: Actor, row: dict[str, Any], code: str) -> str:
        """يُستدعى بعد _load(reserve=True) فقط."""
        if row["status"] != "started":
            raise OnboardingError(409, "onboarding_session_not_startable", details={"status": row["status"]})
        app_id, secret = self._app_credentials()
        try:
            data = await self.graph.exchange_code(code, app_id=app_id, app_secret=secret,
                                                  redirect_uri=self.settings.meta_oauth_redirect_uri)
            token = data["access_token"]
        except (MetaGraphError, KeyError) as exc:
            await self._update(actor, row["id"], status="failed", step="exchange_code", error=str(exc)[:500])
            raise OnboardingError(422, "code_exchange_failed", str(exc)) from exc
        return token

    # ------------------------------------------------------------------ WhatsApp (Embedded Signup)
    async def complete_whatsapp(self, actor: Actor, session_id: UUID, *, state: str, code: str,
                                waba_id: str, phone_number_id: str, coexistence: bool) -> dict[str, Any]:
        row = await self._load(actor, session_id, FLOW_WHATSAPP, state, reserve=True)
        if row["status"] == "completed":
            return {"status": "completed", **_public(row["meta"])}    # نقرة مزدوجة / إعادة إرسال
        if row["token_enc"] is not None:
            return await self.retry(actor, session_id)                 # التوكن موجود: أكمل الخطوات فقط
        token = await self._exchange(actor, row, code)
        meta = {"waba_id": waba_id, "phone_number_id": phone_number_id, "coexistence": coexistence}
        await self._update(actor, session_id, status="token_exchanged", step="token_saved", meta=meta,
                           token_enc=encrypt_token(token), extend_minutes=TOKEN_RETRY_MINUTES)
        return await self._finish_whatsapp(actor, session_id, token, meta)

    async def retry(self, actor: Actor, session_id: UUID) -> dict[str, Any]:
        """إعادة الخطوات بعد التوكن (اشتراك/تسجيل/حفظ) بدون تسجيل دخول جديد في Meta."""
        async with self._db(actor) as s:
            row = (await s.execute(iq.ONBOARDING_FOR_UPDATE, {"id": session_id})).mappings().first()
        if row is None:
            raise OnboardingError(404, "onboarding_session_not_found")
        if row["status"] == "completed":
            return {"status": "completed", **_public(row["meta"])}
        if row["token_enc"] is None or row["expired"]:
            raise OnboardingError(409, "restart_required", "ابدأ الربط من جديد")
        if row["flow"] != FLOW_WHATSAPP:
            raise OnboardingError(409, "use_connect_pages")
        return await self._finish_whatsapp(actor, session_id, decrypt_token(row["token_enc"]),
                                           dict(row["meta"] or {}))

    async def _finish_whatsapp(self, actor: Actor, session_id: UUID, token: str,
                               meta: dict[str, Any]) -> dict[str, Any]:
        waba_id, phone_id = meta["waba_id"], meta["phone_number_id"]
        step = "subscribe_webhooks"
        try:
            await self.graph.subscribe_waba(waba_id, token)
            pin_enc = meta.get("pin_enc")
            if not meta.get("coexistence"):
                # رقم Cloud API جديد: تسجيل مع PIN عشوائي. يُحفظ مشفراً قبل الطلب حتى تستخدم
                # إعادة المحاولة نفس الـ PIN (وليس PIN جديداً لرقم مسجّل فعلاً)
                step = "register_phone"
                if pin_enc is None:
                    pin_enc = base64.b64encode(encrypt_token(f"{secrets.randbelow(10 ** 6):06d}")).decode()
                    await self._update(actor, session_id, status="token_exchanged", step=step,
                                       meta={"pin_enc": pin_enc})
                pin = decrypt_token(base64.b64decode(pin_enc))
                await self.graph.register_phone(phone_id, token, pin)
            step = "verify_phone"
            info = await self.graph.get_phone_number(phone_id, token)
        except MetaGraphError as exc:
            await self._update(actor, session_id, status="failed", step=step, error=f"{exc.code}: {exc.args[0]}"[:500])
            raise OnboardingError(422, "meta_step_failed", str(exc.args[0]),
                                  {"step": step, "meta_code": exc.code, "retry": True}) from exc

        display = f"{info.get('verified_name', '')} {info.get('display_phone_number', '')}".strip() or None
        config = {"onboarding": "self_serve", "coexistence": bool(meta.get("coexistence")),
                  "quality_rating": info.get("quality_rating"), "pin_enc": pin_enc}
        async with self._db(actor) as s:
            res = (await s.execute(iq.UPSERT_CHANNEL, {
                "channel": "whatsapp", "external_id": phone_id, "waba_id": waba_id,
                "display_name": display, "token": encrypt_token(token), "is_test": False,
                "config": json.dumps(config)})).mappings().one()
        if res["outcome"] == "conflict":
            await self._update(actor, session_id, status="failed", step="save_channel",
                               error="channel_owned_by_another_tenant", clear_token=True)
            raise OnboardingError(409, "channel_owned_by_another_tenant",
                                  "هذا الرقم مربوط بحساب آخر. تواصل مع الدعم.")
        result = {"channel_account_id": str(res["channel_account_id"]), "display_name": display,
                  "outcome": res["outcome"]}
        await self._update(actor, session_id, status="completed", step="done", meta=result, clear_token=True)
        log.info("onboarding: whatsapp %s connected for tenant %s (%s)", phone_id, actor.tenant_id, res["outcome"])
        return {"status": "completed", **result,
                "next": "أضف وسيلة دفع في WhatsApp Manager لتفعيل قوالب الرسائل"}

    # ------------------------------------------------------------------ Facebook Login (Pages + IG)
    async def complete_facebook(self, actor: Actor, session_id: UUID, *, state: str, code: str) -> dict[str, Any]:
        row = await self._load(actor, session_id, FLOW_FACEBOOK, state, reserve=True)
        if row["status"] in ("token_exchanged", "completed"):
            return {"status": row["status"], "pages": (row["meta"] or {}).get("pages", [])}
        user_token = await self._exchange(actor, row, code)
        app_id, secret = self._app_credentials()
        try:
            long_lived = (await self.graph.exchange_long_lived(user_token, app_id=app_id,
                                                               app_secret=secret))["access_token"]
            meta_user_id = str((await self.graph.get_me(long_lived)).get("id") or "") or None
            pages = await self.graph.list_pages(long_lived)
        except (MetaGraphError, KeyError) as exc:
            await self._update(actor, session_id, status="failed", step="list_pages", error=str(exc)[:500])
            raise OnboardingError(422, "meta_step_failed", str(exc), {"step": "list_pages"}) from exc

        tokens = {p["id"]: p["access_token"] for p in pages if p.get("id") and p.get("access_token")}
        public = [{"id": p["id"], "name": p.get("name"),
                   "instagram": p.get("instagram_business_account")} for p in pages if p.get("id") in tokens]
        await self._update(actor, session_id, status="token_exchanged", step="choose_pages",
                           meta={"pages": public, "meta_user_id": meta_user_id},
                           token_enc=encrypt_token(json.dumps(tokens)),
                           extend_minutes=TOKEN_RETRY_MINUTES)
        return {"status": "token_exchanged", "pages": public}

    async def connect_pages(self, actor: Actor, session_id: UUID, page_ids: list[str],
                            include_instagram: bool) -> dict[str, Any]:
        row = await self._load(actor, session_id, FLOW_FACEBOOK, None)
        if row["status"] != "token_exchanged" or row["token_enc"] is None or row["expired"]:
            raise OnboardingError(409, "restart_required", "ابدأ الربط من جديد")
        tokens: dict[str, str] = json.loads(decrypt_token(row["token_enc"]))
        pages = {p["id"]: p for p in (row["meta"] or {}).get("pages", [])}
        unknown = [pid for pid in page_ids if pid not in tokens]
        if unknown or not page_ids:
            raise OnboardingError(422, "unknown_pages", details={"page_ids": unknown})

        results: list[dict[str, Any]] = []
        for pid in page_ids:
            token, page = tokens[pid], pages.get(pid, {})
            try:
                await self.graph.subscribe_page(pid, token)
            except MetaGraphError as exc:
                results.append({"page_id": pid, "channel": "messenger", "outcome": "failed",
                                "error": str(exc.args[0])[:200]})
                continue
            # meta_user_id: لتنفيذ Deauthorize / Data Deletion على قنوات هذا المستخدم فقط
            base_cfg = {"onboarding": "self_serve", "meta_user_id": (row["meta"] or {}).get("meta_user_id")}
            targets = [("messenger", pid, page.get("name"), base_cfg)]
            ig = page.get("instagram") or {}
            if include_instagram and ig.get("id"):
                targets.append(("instagram", ig["id"], f"@{ig.get('username')}", {**base_cfg, "page_id": pid}))
            async with self._db(actor) as s:
                for channel, external_id, name, cfg in targets:
                    res = (await s.execute(iq.UPSERT_CHANNEL, {
                        "channel": channel, "external_id": external_id, "waba_id": None,
                        "display_name": name, "token": encrypt_token(token), "is_test": False,
                        "config": json.dumps(cfg)})).mappings().one()
                    results.append({"page_id": pid, "channel": channel, "name": name,
                                    "outcome": res["outcome"],
                                    "channel_account_id": str(res["channel_account_id"]) if res["channel_account_id"] else None})
        ok = any(r["outcome"] in ("created", "updated") for r in results)
        await self._update(actor, session_id, status="completed" if ok else "failed", step="done",
                           meta={"connected": results}, clear_token=ok,
                           error=None if ok else "no_page_connected")
        return {"status": "completed" if ok else "failed", "channels": results}
