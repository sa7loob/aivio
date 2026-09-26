import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.dashboard.logic import (
    LeadPatch,
    LeadPatchError,
    apply_lead_patch,
    decode_cursor,
    encode_cursor,
    leads_to_csv,
    next_cursor,
    normalize_search,
    window_open,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def test_cursor_roundtrip_and_invalid():
    rid = uuid4()
    ts = datetime(2026, 9, 26, 10, 30, 15, 123456, tzinfo=timezone.utc)
    assert decode_cursor(encode_cursor(ts, rid)) == (ts, rid)
    assert decode_cursor(None) == (None, None) and decode_cursor("") == (None, None)
    with pytest.raises(ValueError):
        decode_cursor("bm90LWEtY3Vyc29y")            # "not-a-cursor"


def test_next_cursor_only_on_full_page():
    rows = [{"id": uuid4(), "t": NOW - timedelta(minutes=i)} for i in range(3)]
    assert next_cursor(rows, 5, "t") is None
    assert next_cursor([], 5, "t") is None
    c = next_cursor(rows, 3, "t")
    assert decode_cursor(c) == (rows[-1]["t"], rows[-1]["id"])


def test_reply_window_24h():
    assert window_open(NOW - timedelta(hours=23, minutes=59), NOW)
    assert not window_open(NOW - timedelta(hours=24), NOW)
    assert not window_open(None, NOW)


def _lead(**kw):
    return {"status": "new", "lost_reason": None, "assigned_to": None, "notes": None, **kw}


def test_patch_status_and_events():
    new, ev = apply_lead_patch(_lead(), LeadPatch(status="contacted"))
    assert new["status"] == "contacted" and ev == [("status_changed", {"from": "new", "to": "contacted"})]


def test_patch_noop_has_no_events():
    staff = uuid4()
    new, ev = apply_lead_patch(_lead(status="qualified", assigned_to=staff, notes="x"),
                               LeadPatch(status="qualified", assigned_to=staff, notes="x"))
    assert ev == [] and new["assigned_to"] == staff


def test_patch_lost_requires_reason():
    with pytest.raises(LeadPatchError):
        apply_lead_patch(_lead(), LeadPatch(status="lost"))
    with pytest.raises(LeadPatchError):
        apply_lead_patch(_lead(), LeadPatch(status="lost", lost_reason="   "))
    new, _ = apply_lead_patch(_lead(), LeadPatch(status="lost", lost_reason=" السعر غالي "))
    assert new["lost_reason"] == "السعر غالي"


def test_patch_leaving_lost_clears_reason():
    new, _ = apply_lead_patch(_lead(status="lost", lost_reason="السعر"), LeadPatch(status="qualified"))
    assert new["lost_reason"] is None and new["status"] == "qualified"


def test_patch_invalid_status():
    with pytest.raises(LeadPatchError):
        apply_lead_patch(_lead(), LeadPatch(status="won"))


def test_patch_assign_and_unassign():
    a, b = uuid4(), uuid4()
    new, ev = apply_lead_patch(_lead(assigned_to=a), LeadPatch(assigned_to=b))
    assert new["assigned_to"] == b and ev == [("assigned", {"from": str(a), "to": str(b)})]
    new, ev = apply_lead_patch(_lead(assigned_to=a), LeadPatch(unassign=True))
    assert new["assigned_to"] is None and ev == [("assigned", {"from": str(a), "to": None})]
    _, ev = apply_lead_patch(_lead(), LeadPatch(unassign=True))
    assert ev == []


def test_patch_notes_event_without_content():
    new, ev = apply_lead_patch(_lead(), LeadPatch(notes="يبي يأكد بعد الراتب"))
    assert new["notes"] == "يبي يأكد بعد الراتب" and ev == [("updated", {"field": "notes"})]
    new, ev = apply_lead_patch(_lead(notes="x"), LeadPatch(notes=""))
    assert new["notes"] is None and len(ev) == 1


def test_csv_arabic_bom_phone_and_injection():
    rows = [{"created_at": datetime(2026, 9, 26, 21, 30, tzinfo=timezone.utc), "status": "qualified",
             "full_name": "=HYPERLINK(\"http://x\")", "phone_e164": "+218917778888", "city": "مصراتة",
             "package_title": "عمرة رمضان", "adults": 3, "children": 0, "infants": None,
             "room_type_pref": "quad", "source_channel": "whatsapp", "notes": "-5 خصم", "lost_reason": None,
             "depart_date": None, "preferred_period": Decimal("1.500"), "assigned_name": "علي"}]
    data = leads_to_csv(rows, ZoneInfo("Africa/Tripoli"))
    assert data.startswith(b"\xef\xbb\xbf")
    table = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    header, row = table
    assert header[0] == "تاريخ الطلب" and header[3] == "الهاتف"
    d = dict(zip(header, row))
    assert d["تاريخ الطلب"] == "2026-09-26 23:30"          # توقيت طرابلس UTC+2
    assert d["الحالة"] == "مهتم جداً" and d["الغرفة"] == "رباعي" and d["القناة"] == "واتساب"
    assert d["الهاتف"] == "‎+218917778888"
    assert d["الاسم"].startswith("'=") and d["ملاحظات"].startswith("'-")
    assert d["رضّع"] == "" and d["الفترة المفضلة"] == "1.500"


@pytest.mark.parametrize("raw, expected", [
    ("0913334444", "913334444"),          # الصيغة المحلية المعتادة
    ("091 333 4444", "913334444"),        # كما تعرضها اللوحة
    ("+218 91-333-4444", "218913334444"),
    ("00218913334444", "218913334444"),
    ("3334", "3334"),                     # جزء من الرقم
    ("  أبو محمد ", "أبو محمد"),          # اسم: كما هو
    ("Ali 2", "Ali 2"),
    ("", None), ("   ", None), (None, None), ("000", None),
])
def test_normalize_search(raw, expected):
    assert normalize_search(raw) == expected
