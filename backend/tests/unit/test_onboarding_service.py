"""Onboarding state machine with a fake Meta Graph and an in-memory onboarding_sessions table."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.channels.meta_graph import MetaGraphError
from app.core.config import get_settings
from app.core.crypto import decrypt_token
from app.identity import queries as iq
from app.onboarding.service import FLOW_FACEBOOK, FLOW_WHATSAPP, Actor, OnboardingError, OnboardingService
from tests.unit.fakes import FakeResult


class FakeGraph:
    def __init__(self, fail: dict | None = None, pages=None):
        self.calls: list[tuple] = []
        self.fail = fail or {}
        self.pages = pages or []

    async def _maybe_fail(self, name):
        if name in self.fail:
            code = self.fail.pop(name)
            raise MetaGraphError(f"{name} failed", retryable=False, code=code)

    async def exchange_code(self, code, **kw):
        self.calls.append(("exchange_code", code)); await self._maybe_fail("exchange_code")
        return {"access_token": "BUSINESS_TOKEN"}

    async def exchange_long_lived(self, token, **kw):
        self.calls.append(("long_lived", token)); return {"access_token": "LONG_USER_TOKEN"}

    async def get_me(self, token):
        self.calls.append(("get_me", token)); return {"id": "FBUSER-42"}

    async def list_pages(self, token):
        self.calls.append(("list_pages", token)); return self.pages

    async def subscribe_waba(self, waba_id, token):
        self.calls.append(("subscribe_waba", waba_id)); await self._maybe_fail("subscribe_waba"); return {}

    async def register_phone(self, phone_id, token, pin):
        self.calls.append(("register_phone", phone_id, pin)); await self._maybe_fail("register_phone"); return {}

    async def get_phone_number(self, phone_id, token):
        self.calls.append(("get_phone_number", phone_id))
        return {"verified_name": "وكالة الريان", "display_phone_number": "+218 91 000 0000", "quality_rating": "GREEN"}

    async def subscribe_page(self, page_id, token):
        self.calls.append(("subscribe_page", page_id, token)); await self._maybe_fail(f"subscribe_page:{page_id}"); return {}


class SessionsTable:
    """Minimal stand-in for onboarding_sessions + upsert_channel_account()."""

    def __init__(self, conflict=False):
        self.rows: dict = {}
        self.channels: list[dict] = []
        self.conflict = conflict

    async def execute(self, stmt, p=None):
        if stmt is iq.CREATE_ONBOARDING:
            sid = uuid4()
            self.rows[sid] = {"id": sid, "flow": p["flow"], "state_hash": p["state_hash"], "status": "started",
                              "step": None, "meta": {}, "token_enc": None, "error": None,
                              "expires_at": datetime.now(timezone.utc) + timedelta(minutes=20), "expired": False}
            return FakeResult([{"id": sid, "expires_at": self.rows[sid]["expires_at"]}])
        if stmt in (iq.ONBOARDING_FOR_UPDATE, iq.GET_ONBOARDING):
            row = self.rows.get(p["id"])
            return FakeResult([dict(row)] if row else [])
        if stmt is iq.UPDATE_ONBOARDING:
            row = self.rows[p["id"]]
            row.update(status=p["status"], step=p["step"], error=p["error"])
            row["meta"] = {**row["meta"], **json.loads(p["meta"])}
            row["token_enc"] = None if p["clear_token"] else (p["token_enc"] or row["token_enc"])
            return FakeResult()
        if stmt is iq.UPSERT_CHANNEL:
            if self.conflict:
                return FakeResult([{"channel_account_id": None, "outcome": "conflict"}])
            self.channels.append(p)
            return FakeResult([{"channel_account_id": uuid4(), "outcome": "created"}])
        raise AssertionError(f"unexpected statement: {stmt}")

    def factory(self):
        @asynccontextmanager
        async def _session(tenant_id, user_id):
            yield self
        return _session


def _svc(graph, table):
    settings = get_settings().model_copy(update={
        "meta_app_id": "APPID", "meta_es_config_id": "ES_CFG", "meta_fb_login_config_id": "FB_CFG"})
    return OnboardingService(graph, settings, table.factory())


ACTOR = Actor(uuid4(), uuid4())


def run(coro):
    return asyncio.run(coro)


def _start(svc, flow=FLOW_WHATSAPP):
    return run(svc.start(ACTOR, flow))


def test_whatsapp_happy_path_order_and_secrets():
    g, t = FakeGraph(), SessionsTable()
    svc = _svc(g, t)
    st = _start(svc)
    assert st["config_id"] == "ES_CFG" and st["app_id"] == "APPID" and st["state"].startswith("st_")
    res = run(svc.complete_whatsapp(ACTOR, st["session_id"], state=st["state"], code="CODE" * 5,
                                    waba_id="555", phone_number_id="777", coexistence=False))
    assert res["status"] == "completed" and res["outcome"] == "created"
    assert [c[0] for c in g.calls] == ["exchange_code", "subscribe_waba", "register_phone", "get_phone_number"]
    [ch] = t.channels
    assert ch["channel"] == "whatsapp" and ch["external_id"] == "777" and ch["waba_id"] == "555"
    assert decrypt_token(ch["token"]) == "BUSINESS_TOKEN"            # مشفّر
    row = t.rows[st["session_id"]]
    assert row["token_enc"] is None and row["status"] == "completed"   # التوكن المؤقت مُسح
    # إعادة إرسال نفس الطلب (نقرة مزدوجة) => نفس النتيجة بدون استبدال الـ code مرة ثانية
    again = run(svc.complete_whatsapp(ACTOR, st["session_id"], state=st["state"], code="CODE" * 5,
                                      waba_id="555", phone_number_id="777", coexistence=False))
    assert again["status"] == "completed" and "pin_enc" not in again
    assert [c[0] for c in g.calls].count("exchange_code") == 1


def test_state_mismatch_and_unknown_session():
    svc = _svc(FakeGraph(), SessionsTable())
    st = _start(svc)
    with pytest.raises(OnboardingError) as ei:
        run(svc.complete_whatsapp(ACTOR, st["session_id"], state="st_wrong-state-value", code="C" * 20,
                                  waba_id="1", phone_number_id="2", coexistence=False))
    assert ei.value.http_status == 403
    with pytest.raises(OnboardingError) as ei:
        run(svc.status(ACTOR, uuid4()))
    assert ei.value.http_status == 404


def test_code_exchange_failure_marks_failed_and_blocks_reuse():
    g, t = FakeGraph(fail={"exchange_code": 100}), SessionsTable()
    svc = _svc(g, t)
    st = _start(svc)
    with pytest.raises(OnboardingError) as ei:
        run(svc.complete_whatsapp(ACTOR, st["session_id"], state=st["state"], code="C" * 20,
                                  waba_id="1", phone_number_id="2", coexistence=False))
    assert ei.value.code == "code_exchange_failed"
    assert t.rows[st["session_id"]]["status"] == "failed"
    with pytest.raises(OnboardingError) as ei:          # نفس الجلسة لا تُستخدم مجدداً
        run(svc.complete_whatsapp(ACTOR, st["session_id"], state=st["state"], code="C" * 20,
                                  waba_id="1", phone_number_id="2", coexistence=False))
    assert ei.value.http_status == 409


def test_register_failure_then_retry_reuses_same_pin():
    g, t = FakeGraph(fail={"register_phone": 133005}), SessionsTable()
    svc = _svc(g, t)
    st = _start(svc)
    with pytest.raises(OnboardingError) as ei:
        run(svc.complete_whatsapp(ACTOR, st["session_id"], state=st["state"], code="C" * 20,
                                  waba_id="555", phone_number_id="777", coexistence=False))
    assert ei.value.details["step"] == "register_phone" and ei.value.details["retry"] is True
    assert t.rows[st["session_id"]]["token_enc"] is not None           # التوكن محفوظ للاستئناف
    res = run(svc.retry(ACTOR, st["session_id"]))
    assert res["status"] == "completed"
    pins = [c[2] for c in g.calls if c[0] == "register_phone"]
    assert len(pins) == 2 and pins[0] == pins[1] and len(pins[0]) == 6
    assert [c[0] for c in g.calls].count("exchange_code") == 1


def test_coexistence_skips_registration():
    g, t = FakeGraph(), SessionsTable()
    svc = _svc(g, t)
    st = _start(svc)
    run(svc.complete_whatsapp(ACTOR, st["session_id"], state=st["state"], code="C" * 20,
                              waba_id="555", phone_number_id="777", coexistence=True))
    assert "register_phone" not in [c[0] for c in g.calls]
    assert json.loads(t.channels[0]["config"])["coexistence"] is True


def test_channel_owned_by_other_tenant_is_rejected():
    svc = _svc(FakeGraph(), SessionsTable(conflict=True))
    st = _start(svc)
    with pytest.raises(OnboardingError) as ei:
        run(svc.complete_whatsapp(ACTOR, st["session_id"], state=st["state"], code="C" * 20,
                                  waba_id="555", phone_number_id="777", coexistence=False))
    assert ei.value.http_status == 409 and ei.value.code == "channel_owned_by_another_tenant"


def test_facebook_pages_and_instagram():
    pages = [{"id": "P1", "name": "صفحة الريان", "access_token": "PT1",
              "instagram_business_account": {"id": "IG1", "username": "rayan.travel"}},
             {"id": "P2", "name": "صفحة ثانية", "access_token": "PT2"}]
    g, t = FakeGraph(pages=pages, fail={"subscribe_page:P2": 200}), SessionsTable()
    svc = _svc(g, t)
    st = _start(svc, FLOW_FACEBOOK)
    assert st["config_id"] == "FB_CFG"
    res = run(svc.complete_facebook(ACTOR, st["session_id"], state=st["state"], code="C" * 20))
    assert [p["id"] for p in res["pages"]] == ["P1", "P2"]
    assert "access_token" not in json.dumps(res)                        # التوكنات لا تعود للواجهة
    assert ("long_lived", "BUSINESS_TOKEN") in g.calls

    with pytest.raises(OnboardingError):
        run(svc.connect_pages(ACTOR, st["session_id"], ["NOT_MINE"], True))
    out = run(svc.connect_pages(ACTOR, st["session_id"], ["P1", "P2"], True))
    assert out["status"] == "completed"
    outcomes = {(c["channel"], c["page_id"]): c["outcome"] for c in out["channels"]}
    assert outcomes == {("messenger", "P1"): "created", ("instagram", "P1"): "created", ("messenger", "P2"): "failed"}
    ig = next(c for c in t.channels if c["channel"] == "instagram")
    assert ig["external_id"] == "IG1" and json.loads(ig["config"])["page_id"] == "P1"
    assert decrypt_token(ig["token"]) == "PT1"
    # الربط مسجّل باسم مستخدم فيسبوك (لـ Deauthorize / Data Deletion) ولا يظهر للواجهة
    assert all(json.loads(c["config"])["meta_user_id"] == "FBUSER-42" for c in t.channels)
    assert "FBUSER-42" not in json.dumps(run(svc.status(ACTOR, st["session_id"])), default=str)


def test_start_requires_meta_configuration():
    settings = get_settings().model_copy(update={"meta_app_id": None})
    svc = OnboardingService(FakeGraph(), settings, SessionsTable().factory())
    with pytest.raises(OnboardingError) as ei:
        run(svc.start(ACTOR, FLOW_WHATSAPP))
    assert ei.value.http_status == 503
