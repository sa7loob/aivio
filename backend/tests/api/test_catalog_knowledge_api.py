"""Catalog + knowledge endpoints: roles, upload validation, publish rules, corrections, idempotency.
قاعدة البيانات مستبدلة بجلسة وهمية؛ السلوك الفعلي لقاعدة البيانات في tests/sql/13."""
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.ai import queries as aq
from app.api import deps
from app.api.v1 import catalog as catalog_api
from app.api.v1 import knowledge as knowledge_api
from app.main import create_app
from tests.unit.fakes import FakeResult

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
TENANT = uuid4()
USER = uuid4()


class FakeSession:
    def __init__(self, responses):
        self.responses, self.executed = responses, []

    async def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        resp = self.responses.get(id(stmt))
        return resp(params) if callable(resp) else (resp or FakeResult())

    def calls_to(self, stmt):
        return [p for s, p in self.executed if s is stmt]


@pytest.fixture
def api(monkeypatch):
    """api(role, {stmt: FakeResult}) => (client, session)."""
    def make(role="owner", responses=None):
        session = FakeSession({id(k): v for k, v in (responses or {}).items()})

        @asynccontextmanager
        async def fake_tenant_session(tenant_id, user_id=None):
            yield session

        monkeypatch.setattr(catalog_api, "tenant_session", fake_tenant_session)
        monkeypatch.setattr(knowledge_api, "tenant_session", fake_tenant_session)
        app = create_app()
        user = deps.CurrentUser(USER, "u@x.ly", "موظف", None, uuid4())
        app.dependency_overrides[deps.tenant_context] = lambda: deps.TenantContext(user, TENANT, role)
        return TestClient(app), session
    return make


def _upload(client, data=PNG, ctype="image/png", name="b.png"):
    return client.post(f"/api/v1/catalog/imports?filename={name}", content=data, headers={"Content-Type": ctype})


# ------------------------------------------------------------------ roles
@pytest.mark.parametrize("method,path", [
    ("post", "/api/v1/catalog/imports"),
    ("get", "/api/v1/catalog/imports"),
    ("post", f"/api/v1/catalog/packages/{uuid4()}/publish"),
    ("delete", f"/api/v1/catalog/packages/{uuid4()}"),
    ("patch", f"/api/v1/catalog/prices/{uuid4()}"),
    ("delete", f"/api/v1/catalog/departures/{uuid4()}"),
    ("delete", f"/api/v1/knowledge/{uuid4()}"),
])
def test_agent_cannot_manage_catalog_or_deactivate_knowledge(api, method, path):
    client, session = api("agent")
    kwargs = {"json": {}} if method == "patch" else {}
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "insufficient_role"
    assert session.executed == []


def test_agent_can_read_catalog_and_save_knowledge(api):
    client, _ = api("agent", {aq.LIST_PACKAGES: FakeResult([]), aq.CONVERSATION_EXISTS: FakeResult([{"x": 1}]),
                              aq.INSERT_STAFF_KNOWLEDGE: FakeResult([{"id": uuid4(), "created_at": "t"}])})
    assert client.get("/api/v1/catalog/packages?status=draft").status_code == 200
    r = client.post(f"/api/v1/conversations/{uuid4()}/knowledge", json={"question": "سؤال؟", "answer": "جواب"})
    assert r.status_code == 201


# ------------------------------------------------------------------ upload
@pytest.mark.parametrize("data,ctype,status,code", [
    (b"GIF89a....", "image/png", 415, "unsupported_file_type"),
    (PNG, "application/pdf", 415, "content_type_mismatch"),
    (b"", "image/png", 422, "empty_file"),
])
def test_upload_rejected(api, data, ctype, status, code):
    client, session = api()
    r = _upload(client, data, ctype)
    assert r.status_code == status and r.json()["detail"]["error"] == code and r.json()["detail"]["message"]
    assert session.executed == []


def test_upload_too_large(api, monkeypatch):
    client, session = api()
    settings = catalog_api.get_settings().model_copy(update={"catalog_import_max_bytes": 16})
    monkeypatch.setattr(catalog_api, "get_settings", lambda: settings)
    r = _upload(client)
    assert r.status_code == 413 and r.json()["detail"]["error"] == "file_too_large"


def test_upload_creates_import_and_job(api):
    new_id = uuid4()
    client, session = api("admin", {aq.FIND_IMPORT_BY_SHA: FakeResult([]),
                                    aq.INSERT_CATALOG_IMPORT: FakeResult([{"id": new_id, "status": "pending",
                                                                           "created_at": "t"}])})
    r = _upload(client, ctype="application/octet-stream", name="../../بروشور.png")
    assert r.status_code == 202 and r.json() == {"id": str(new_id), "status": "pending", "duplicate": False}
    ins = session.calls_to(aq.INSERT_CATALOG_IMPORT)[0]
    assert ins["mime_type"] == "image/png" and ins["file_data"] == PNG and ins["size_bytes"] == len(PNG)
    assert ins["filename"] == "بروشور.png" and len(ins["sha256"]) == 64
    assert session.calls_to(aq.ENQUEUE_CATALOG_EXTRACTION) == [{"catalog_import_id": new_id}]


def test_upload_duplicate_returns_existing(api):
    old = uuid4()
    client, session = api("owner", {aq.FIND_IMPORT_BY_SHA: FakeResult([{"id": old, "status": "done",
                                                                        "created_at": "t"}])})
    r = _upload(client)
    assert r.status_code == 200 and r.json() == {"id": str(old), "status": "done", "duplicate": True}
    assert session.calls_to(aq.INSERT_CATALOG_IMPORT) == [] and session.calls_to(aq.ENQUEUE_CATALOG_EXTRACTION) == []


# ------------------------------------------------------------------ publish / discard / corrections
@pytest.mark.parametrize("row,status,code", [
    (None, 404, "package_not_found"),
    ({"id": "P", "status": "active", "adult_prices": 3}, 409, "package_not_draft"),
    ({"id": "P", "status": "draft", "adult_prices": 0}, 422, "package_has_no_prices"),
])
def test_publish_rules(api, row, status, code):
    client, session = api("admin", {aq.PACKAGE_FOR_PUBLISH: FakeResult([row] if row else [])})
    r = client.post(f"/api/v1/catalog/packages/{uuid4()}/publish")
    assert r.status_code == status and r.json()["detail"]["error"] == code
    assert session.calls_to(aq.PUBLISH_PACKAGE) == []


def test_publish_confirms_prices(api):
    pid = uuid4()
    client, session = api("admin", {aq.PACKAGE_FOR_PUBLISH: FakeResult([{"id": pid, "status": "draft",
                                                                         "adult_prices": 2}])})
    r = client.post(f"/api/v1/catalog/packages/{pid}/publish")
    assert r.status_code == 200 and r.json() == {"id": str(pid), "status": "active"}
    assert session.calls_to(aq.PUBLISH_PACKAGE) == [{"id": pid}]
    assert session.calls_to(aq.CONFIRM_PACKAGE_PRICES) == [{"id": pid}]


def test_discard_only_drafts(api):
    client, session = api("admin", {aq.PACKAGE_FOR_PUBLISH: FakeResult([{"id": "P", "status": "active",
                                                                         "adult_prices": 1}])})
    assert client.delete(f"/api/v1/catalog/packages/{uuid4()}").status_code == 409
    assert session.calls_to(aq.DELETE_DRAFT_PACKAGE) == []
    client, session = api("admin", {aq.PACKAGE_FOR_PUBLISH: FakeResult([{"id": "P", "status": "draft",
                                                                         "adult_prices": 0}])})
    assert client.delete(f"/api/v1/catalog/packages/{uuid4()}").status_code == 204
    assert len(session.calls_to(aq.DELETE_DRAFT_PACKAGE)) == 1


def test_price_patch_notes_only_when_sent(api):
    pid = uuid4()
    row = {"id": pid, "package_id": uuid4(), "departure_id": None, "room_type": "quad", "traveler_type": "adult",
           "amount": 4400, "currency": "LYD", "notes": None, "updated_at": "t"}
    client, session = api("admin", {aq.PRICE_FOR_UPDATE: FakeResult([{"id": pid, "package_id": "P",
                                                                      "package_status": "draft"}]),
                                    aq.UPDATE_PRICE: FakeResult([row])})
    assert client.patch(f"/api/v1/catalog/prices/{pid}", json={"amount": "4400.50"}).status_code == 200
    assert client.patch(f"/api/v1/catalog/prices/{pid}", json={"notes": None}).status_code == 200
    first, second = session.calls_to(aq.UPDATE_PRICE)
    assert str(first["amount"]) == "4400.50" and first["set_notes"] is False
    assert second["amount"] is None and second["set_notes"] is True and second["notes"] is None
    for bad in ({"amount": -1}, {"amount": "1.234"}, {"amount": 1_000_000}, {"currency": "USD"}):
        assert client.patch(f"/api/v1/catalog/prices/{pid}", json=bad).status_code == 422


def test_corrections_refused_after_publish(api):
    client, session = api("admin", {
        aq.PRICE_FOR_UPDATE: FakeResult([{"id": "X", "package_id": "P", "package_status": "active"}]),
        aq.DEPARTURE_FOR_UPDATE: FakeResult([{"id": "D", "package_id": "P", "package_status": "active"}])})
    assert client.patch(f"/api/v1/catalog/prices/{uuid4()}", json={"amount": 1}).json()["detail"]["error"] == "package_not_draft"
    assert client.delete(f"/api/v1/catalog/prices/{uuid4()}").status_code == 409
    assert client.delete(f"/api/v1/catalog/departures/{uuid4()}").status_code == 409
    assert session.calls_to(aq.UPDATE_PRICE) == session.calls_to(aq.DELETE_PRICE) == []
    assert session.calls_to(aq.DELETE_DEPARTURE) == []


# ------------------------------------------------------------------ knowledge
def _conv_messages():
    ids = [uuid4() for _ in range(3)]
    rows = [  # الأحدث أولاً كما يعيدها CONVERSATION_RECENT_MESSAGES
        {"id": ids[2], "direction": "outbound", "sender_type": "staff", "msg_type": "text", "text_content": "نعم"},
        {"id": ids[1], "direction": "inbound", "sender_type": "customer", "msg_type": "text",
         "text_content": "تقبلوا التقسيط؟"},
        {"id": ids[0], "direction": "outbound", "sender_type": "bot", "msg_type": "text", "text_content": "هلا"},
    ]
    return rows, ids


def test_knowledge_suggestion(api):
    rows, ids = _conv_messages()
    client, _ = api("agent", {aq.CONVERSATION_EXISTS: FakeResult([{"x": 1}]),
                              aq.CONVERSATION_RECENT_MESSAGES: FakeResult(rows)})
    r = client.get(f"/api/v1/conversations/{uuid4()}/knowledge-suggestion")
    assert r.status_code == 200 and r.json() == {"question": "تقبلوا التقسيط؟", "answer": "نعم",
                                                 "message_id": str(ids[2])}
    client, _ = api("agent", {aq.CONVERSATION_EXISTS: FakeResult([{"x": 1}]),
                              aq.CONVERSATION_RECENT_MESSAGES: FakeResult(rows[1:])})
    assert client.get(f"/api/v1/conversations/{uuid4()}/knowledge-suggestion").json()["detail"]["error"] == "no_staff_answer"
    client, _ = api("agent", {aq.CONVERSATION_EXISTS: FakeResult([])})
    assert client.get(f"/api/v1/conversations/{uuid4()}/knowledge-suggestion").status_code == 404


def test_knowledge_save_idempotent_per_reply(api):
    conv, msg, existing = uuid4(), uuid4(), uuid4()
    client, session = api("agent", {aq.CONVERSATION_EXISTS: FakeResult([{"x": 1}]),
                                    aq.KNOWLEDGE_BY_SOURCE_REF: FakeResult([{"id": existing, "created_at": "t"}])})
    r = client.post(f"/api/v1/conversations/{conv}/knowledge",
                    json={"question": "سؤال؟", "answer": "جواب", "message_id": str(msg)})
    assert r.status_code == 200 and r.json() == {"id": str(existing), "duplicate": True}
    assert session.calls_to(aq.KNOWLEDGE_BY_SOURCE_REF) == [{"source_ref": f"conversation:{conv}:message:{msg}"}]
    assert session.calls_to(aq.INSERT_STAFF_KNOWLEDGE) == []

    new_id = uuid4()
    client, session = api("agent", {aq.CONVERSATION_EXISTS: FakeResult([{"x": 1}]),
                                    aq.KNOWLEDGE_BY_SOURCE_REF: FakeResult([]),
                                    aq.INSERT_STAFF_KNOWLEDGE: FakeResult([{"id": new_id, "created_at": "t"}])})
    r = client.post(f"/api/v1/conversations/{conv}/knowledge",
                    json={"question": "  هل تقبلون   التقسيط؟ ", "answer": " نعم على دفعتين ", "message_id": str(msg)})
    assert r.status_code == 201 and r.json()["embedding"] == "pending"
    ins = session.calls_to(aq.INSERT_STAFF_KNOWLEDGE)[0]
    assert ins["title"] == "هل تقبلون التقسيط؟" and ins["content"] == "نعم على دفعتين"
    assert session.calls_to(aq.ENQUEUE_EMBEDDING) == [{"knowledge_chunk_id": new_id}]


@pytest.mark.parametrize("body", [{"question": "؟", "answer": "نعم"}, {"question": "سؤال؟", "answer": ""},
                                  {"question": "سؤال؟", "answer": "x" * 901}, {"question": "   ", "answer": "نعم"},
                                  {"question": "سؤال؟", "answer": "نعم", "extra": 1}])
def test_knowledge_validation(api, body):
    client, session = api("agent", {aq.CONVERSATION_EXISTS: FakeResult([{"x": 1}])})
    assert client.post(f"/api/v1/conversations/{uuid4()}/knowledge", json=body).status_code == 422
    assert session.calls_to(aq.INSERT_STAFF_KNOWLEDGE) == []


def test_knowledge_deactivate(api):
    client, _ = api("admin", {aq.DEACTIVATE_KNOWLEDGE: FakeResult([{"id": "K"}])})
    assert client.delete(f"/api/v1/knowledge/{uuid4()}").status_code == 204
    client, _ = api("admin", {aq.DEACTIVATE_KNOWLEDGE: FakeResult([])})
    assert client.delete(f"/api/v1/knowledge/{uuid4()}").status_code == 404
