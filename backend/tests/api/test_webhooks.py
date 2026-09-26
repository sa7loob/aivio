"""Webhook endpoint tests — DB is replaced by an in-memory fake (no Postgres needed)."""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api import webhooks
from app.channels.meta_signature import compute_signature
from app.main import create_app

SECRET = "test-app-secret"


class FakeSession:
    def __init__(self, store, fail=False):
        self.store, self.fail = store, fail

    async def execute(self, stmt, params=None):
        if self.fail:
            raise OperationalError("INSERT", {}, Exception("db down"))
        self.store.append(params)


@pytest.fixture
def stored(monkeypatch):
    rows: list[dict] = []

    @asynccontextmanager
    async def fake_system_session():
        yield FakeSession(rows)

    monkeypatch.setattr(webhooks, "system_session", fake_system_session)
    return rows


@pytest.fixture
def client():
    return TestClient(create_app())


def _post(client, body: bytes, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhooks/meta", content=body, headers=headers)


def test_verify_subscription_ok(client):
    r = client.get("/webhooks/meta", params={
        "hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "12345"})
    assert r.status_code == 200 and r.text == "12345"


def test_verify_subscription_wrong_token(client):
    r = client.get("/webhooks/meta", params={
        "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "12345"})
    assert r.status_code == 403


def test_valid_event_is_stored_raw(client, stored, load_fixture):
    body = json.dumps(load_fixture("whatsapp_messages.json")).encode()  # \u escapes مثل Meta
    r = _post(client, body, compute_signature(body, SECRET))
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert len(stored) == 1
    assert stored[0]["object_type"] == "whatsapp_business_account"
    assert stored[0]["payload"] == body.decode()   # نفس البايتات بدون إعادة تسلسل


def test_invalid_signature_rejected_and_not_stored(client, stored):
    body = b'{"object":"whatsapp_business_account"}'
    assert _post(client, body, "sha256=" + "0" * 64).status_code == 401
    assert _post(client, body, None).status_code == 401
    assert stored == []


def test_invalid_json_rejected(client, stored):
    body = b"not json"
    assert _post(client, body, compute_signature(body, SECRET)).status_code == 400
    assert stored == []


def test_db_failure_returns_503_so_meta_retries(client, monkeypatch):
    @asynccontextmanager
    async def failing_session():
        yield FakeSession([], fail=True)

    monkeypatch.setattr(webhooks, "system_session", failing_session)
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    assert _post(client, body, compute_signature(body, SECRET)).status_code == 503
