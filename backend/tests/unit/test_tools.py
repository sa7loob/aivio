import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.agent.phone import normalize_phone
from app.agent.tools import (
    CreateLeadArgs,
    PackageDetailsArgs,
    SearchPackagesArgs,
    ToolUserError,
    _template_param,
    create_lead,
    default_registry,
    get_package_details,
    search_packages,
)
from app.db import queries as q
from tests.unit.fakes import FakeDB, FakeResult, make_ctx


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ schema
def test_tool_specs_are_clean_json_schema():
    specs = {s.name: s for s in default_registry().specs()}
    assert set(specs) == {"search_packages", "get_package_details", "search_knowledge",
                          "create_lead", "handoff_to_human"}
    assert '"title"' not in json.dumps([s.parameters for s in specs.values()])
    lead = specs["create_lead"].parameters
    assert lead["required"] == ["full_name"]
    assert lead["additionalProperties"] is False
    room = json.dumps(lead["properties"]["room_type_pref"])
    assert "double" in room and "quad" in room


# ------------------------------------------------------------------ phone
@pytest.mark.parametrize("raw,expected", [
    ("0913334444", "+218913334444"),
    ("091-333 4444", "+218913334444"),
    ("٠٩١٣٣٣٤٤٤٤", "+218913334444"),
    ("00218913334444", "+218913334444"),
    ("+218 91 333 4444", "+218913334444"),
    ("218913334444", "+218913334444"),
    ("913334444", "+218913334444"),
    ("+201001234567", "+201001234567"),
    ("12345", None),
    ("0213334444", None),        # أرضي/غير موبايل
    ("abc", None),
    ("", None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_template_param_sanitized():
    assert _template_param("علي\n\n   الورفلي") == "علي الورفلي"
    assert _template_param("") == "-"
    assert len(_template_param("x" * 200)) == 60


# ------------------------------------------------------------------ search_packages
def test_search_packages_no_results_offers_available():
    db = FakeDB({
        q.TOOL_SEARCH_PACKAGES: FakeResult([]),
        q.TOOL_LIST_ACTIVE_PACKAGES: FakeResult([{"package_id": uuid4(), "title": "عمرة", "kind": "umrah"}]),
    })
    out = _run(search_packages(make_ctx(db), SearchPackagesArgs(query="ماليزيا")))
    assert out["results"] == [] and len(out["available_packages"]) == 1
    assert db.calls_to(q.TOOL_SEARCH_PACKAGES)[0] == {"query": "ماليزيا", "kind": None, "limit": 5}


# ------------------------------------------------------------------ get_package_details
def _pkg_row(pid):
    return {"id": pid, "kind": "umrah", "title": "عمرة المولد", "season_label": None,
            "description": None, "status": "active", "duration_days": 15, "nights_makkah": 10,
            "nights_madinah": 5, "departure_city": "طرابلس", "airline": None, "includes": [],
            "excludes": [], "requirements": "جواز", "booking_terms": None, "agent_notes": None}


def test_package_details_flags_stale_prices():
    pid = uuid4()
    old = datetime.now(timezone.utc) - timedelta(days=40)
    db = FakeDB({
        q.TOOL_PACKAGE: FakeResult([_pkg_row(pid)]),
        q.TOOL_PACKAGE_DEPARTURES: FakeResult([{"id": uuid4(), "depart_date": date(2026, 11, 1),
                                                "return_date": date(2026, 11, 15),
                                                "registration_deadline": None, "status": "few_left"}]),
        q.TOOL_PACKAGE_PRICES: FakeResult([{"room_type": "quad", "traveler_type": "adult",
                                            "amount": Decimal("6500.00"), "currency": "LYD",
                                            "notes": None, "updated_at": old, "departure_date": None}]),
    })
    out = _run(get_package_details(make_ctx(db), PackageDetailsArgs(package_id=pid)))
    assert out["prices"][0]["room"] == "رباعي"
    assert out["departures"][0]["availability"] == "باقي مقاعد قليلة"
    assert "price_notice" in out


def test_package_details_missing():
    db = FakeDB({q.TOOL_PACKAGE: FakeResult([])})
    with pytest.raises(ToolUserError):
        _run(get_package_details(make_ctx(db), PackageDetailsArgs(package_id=uuid4())))


# ------------------------------------------------------------------ create_lead
def _lead_db(inserted=True, staff=True, account=True):
    lead_id = uuid4()
    return lead_id, FakeDB({
        q.TOOL_PACKAGE_TITLE: FakeResult(scalar="عمرة المولد النبوي"),
        q.TOOL_DEPARTURE_BELONGS: FakeResult([{"?column?": 1}]),
        q.TOOL_UPSERT_LEAD: FakeResult([{"id": lead_id, "inserted": inserted}]),
        q.TOOL_STAFF_TO_NOTIFY: FakeResult([{"id": uuid4(), "full_name": "سالم",
                                             "whatsapp_phone": "+218911112222"}] if staff else []),
        q.TOOL_NOTIFY_SENDER_ACCOUNT: FakeResult(scalar=uuid4() if account else None),
    })


def test_create_lead_new_enqueues_staff_template():
    lead_id, db = _lead_db()
    ctx = make_ctx(db)
    args = CreateLeadArgs(full_name="علي الورفلي", package_id=uuid4(), adults=2,
                          room_type_pref="double")
    out = _run(create_lead(ctx, args))

    assert out["status"] == "created" and out["sales_team_notified"] is True
    assert ctx.lead_id == lead_id
    upsert = db.calls_to(q.TOOL_UPSERT_LEAD)[0]
    assert upsert["phone_e164"] == "+218913334444"          # من رقم الواتساب
    assert upsert["source_channel"] == "whatsapp"
    [notif] = db.calls_to(q.ENQUEUE_OUTBOUND)
    assert notif["purpose"] == "staff_notification" and notif["kind"] == "template"
    assert notif["recipient"] == "218911112222"
    body = json.loads(notif["body"])
    assert body["name"] == "new_lead"
    assert body["params"] == ["علي الورفلي", "+218913334444", "عمرة المولد النبوي", "2 بالغ، غرفة ثنائي"]
    assert len(db.sessions_opened) == 1                      # كل شيء في transaction واحدة


def test_create_lead_update_does_not_renotify():
    _, db = _lead_db(inserted=False)
    out = _run(create_lead(make_ctx(db), CreateLeadArgs(full_name="علي", adults=3)))
    assert out["status"] == "updated"
    assert db.calls_to(q.ENQUEUE_OUTBOUND) == []


def test_create_lead_without_staff_records_failure_event():
    _, db = _lead_db(staff=False)
    out = _run(create_lead(make_ctx(db), CreateLeadArgs(full_name="علي")))
    assert out["sales_team_notified"] is False
    events = [json.loads(p["data"]) for p in db.calls_to(q.TOOL_INSERT_LEAD_EVENT)
              if p["event_type"] == "notification_failed"]
    assert events == [{"reason": "no_staff_with_whatsapp"}]


def test_create_lead_phone_rules():
    _, db = _lead_db()
    # رقم مكتوب من الزبون يُوحّد
    _run(create_lead(make_ctx(db), CreateLeadArgs(full_name="علي", phone="٠٩٢٥٥٥٦٦٦٦")))
    assert db.calls_to(q.TOOL_UPSERT_LEAD)[0]["phone_e164"] == "+218925556666"
    # إنستغرام بدون رقم معروف => خطأ مفهوم للموديل
    with pytest.raises(ToolUserError):
        _run(create_lead(make_ctx(db, phone=None, channel="instagram"),
                         CreateLeadArgs(full_name="علي")))
    with pytest.raises(ToolUserError):
        _run(create_lead(make_ctx(db), CreateLeadArgs(full_name="علي", phone="123")))


def test_create_lead_rejects_bad_package():
    db = FakeDB({q.TOOL_PACKAGE_TITLE: FakeResult(scalar=None)})
    with pytest.raises(ToolUserError):
        _run(create_lead(make_ctx(db), CreateLeadArgs(full_name="علي", package_id=uuid4())))
