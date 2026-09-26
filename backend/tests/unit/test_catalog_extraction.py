"""Brochure import: upload validation, strict schema, normalization rules, draft persistence, handler."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.ai import catalog as cat
from app.ai import queries as aq
from app.ai.jobs import AIDeps, Job, PermanentJobError
from app.core.config import get_settings
from app.llm.base import StructuredResult, Usage
from scripts.dev_mock_upstream import catalog_extraction
from tests.unit.fakes import FakeResult

TODAY = date(2026, 9, 26)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ upload
@pytest.mark.parametrize("data,expected", [
    (b"\xff\xd8\xff\xe0rest", "image/jpeg"), (PNG, "image/png"),
    (b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image/webp"), (b"%PDF-1.7\n", "application/pdf"),
    (b"GIF89a", None), (b"", None),
])
def test_sniff_document(data, expected):
    assert cat.sniff_document(data) == expected


def test_validate_upload_codes():
    assert cat.validate_upload(PNG, "image/png", 100) == "image/png"
    assert cat.validate_upload(PNG, "application/octet-stream", 100) == "image/png"   # الهيدر غير المحدد مقبول
    assert cat.validate_upload(PNG, None, 100) == "image/png"
    for data, ctype, code in [(b"", "image/png", "empty_file"), (PNG, "image/png", "file_too_large"),
                              (b"GIF89a....", "image/png", "unsupported_file_type"),
                              (PNG, "application/pdf", "content_type_mismatch")]:
        with pytest.raises(cat.UploadError) as e:
            cat.validate_upload(data, ctype, 20 if code == "file_too_large" else 100)
        assert str(e.value) == code


def test_safe_filename():
    assert cat.safe_filename("C:\\Users\\x\\بروشور رمضان.png") == "بروشور رمضان.png"
    assert cat.safe_filename("../../etc/passwd") == "passwd"
    assert cat.safe_filename("a\x00b\n.png") == "ab.png"
    assert cat.safe_filename("") is None and cat.safe_filename(None) is None
    assert len(cat.safe_filename("x" * 500)) == 200


# ------------------------------------------------------------------ schema (Structured Outputs strict)
def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


def test_schema_is_strict_compatible():
    objs = list(_objects(cat.CATALOG_SCHEMA))
    assert len(objs) == 5                                   # الجذر، البرنامج، الفندق، الموعد، السعر
    for o in objs:
        assert o["additionalProperties"] is False
        assert set(o["required"]) == set(o["properties"]), "strict: every property must be required"


# ------------------------------------------------------------------ normalization
def test_normalize_mock_catalog_end_to_end():
    today = date.today()
    n = cat.normalize_extraction(catalog_extraction(), today)
    ramadan, mawlid = n.packages
    assert ramadan.title == "عمرة رمضان - 15 يوم" and ramadan.kind == "umrah" and ramadan.duration_days == 15
    assert [h.city for h in ramadan.hotels] == ["makkah", "madinah"] and ramadan.aliases == ["عمرة شهر رمضان"]
    # d2 بدون تاريخ عودة => محسوب من المدة (15 يوماً) مع تحذير
    assert [(d.ref, (d.return_date - d.depart_date).days) for d in ramadan.departures] == [("d1", 14), ("d2", 14)]
    assert ramadan.departures[0].seats_total == 45
    assert {(p.room_type, p.traveler_type, p.amount) for p in ramadan.prices} == {
        ("quad", "adult", Decimal("4500.00")), ("triple", "adult", Decimal("5200.00")),
        ("double", "adult", Decimal("6100.00")), ("quad", "child", Decimal("3500.00"))}   # USD حُذف
    assert mawlid.departures == [] and [p.amount for p in mawlid.prices] == [Decimal("3900.00")]
    text = "\n".join(n.warnings)
    assert "بعملة USD لم يُحفظ" in text and "محسوب من مدة البرنامج" in text
    assert "موعد بدون تاريخ ميلادي صريح" in text and "اعتبرناها دينار ليبي" in text
    assert "ملاحظة من قراءة الملف" in text


def _pkg(**kw):
    base = {"title": "عمرة", "kind": "umrah", "departures": [], "prices": [], "hotels": [], "aliases": []}
    return {**base, **kw}


def _price(amount, room="quad", traveler="adult", currency="LYD", ref=None):
    return {"room_type": room, "traveler_type": traveler, "amount": amount, "currency": currency,
            "departure_ref": ref, "notes": None}


def test_amount_and_currency_parsing():
    n = cat.normalize_extraction({"packages": [_pkg(prices=[
        _price("٤٬٥٠٠", currency="د.ل"), _price("5,200", room="ثلاثي", currency="دينار ليبي"),
        _price(-1, room="double"), _price(2_000_000, room="single"), _price("abc", room="shared"),
        _price(100, room="suite"), _price(900, room="double", currency="EUR"),
    ])]}, TODAY)
    prices = n.packages[0].prices
    assert [(p.room_type, p.amount) for p in prices] == [("quad", Decimal("4500.00")), ("triple", Decimal("5200.00"))]
    text = "\n".join(n.warnings)
    assert text.count("سعر غير واضح") == 3 and "غير معروفة" in text and "بعملة EUR" in text


def test_duplicate_prices_and_prices_of_dropped_departures():
    future = (TODAY + timedelta(days=30)).isoformat()
    n = cat.normalize_extraction({"packages": [_pkg(
        departures=[{"ref": "d1", "depart_date": future, "return_date": future, "seats_total": None, "notes": None},
                    {"ref": "d2", "depart_date": "2020-01-01", "return_date": "2020-01-10", "seats_total": None,
                     "notes": None}],
        prices=[_price(100), _price(150), _price(200, ref="d1"), _price(300, ref="d2"), _price(400, ref="d9")],
    )]}, TODAY)
    p = n.packages[0]
    assert [(x.amount, x.departure_ref) for x in p.prices] == [(Decimal("100.00"), None), (Decimal("200.00"), "d1")]
    text = "\n".join(n.warnings)
    assert "سعر مكرر" in text and text.count("مرتبط بموعد لم يُحفظ") == 2 and "انتهى" in text


@pytest.mark.parametrize("dep,warning", [
    ({"depart_date": "2026-13-40", "return_date": None}, "بدون تاريخ ميلادي"),
    ({"depart_date": "2035-01-01", "return_date": "2035-01-10"}, "بعيد جداً"),
    ({"depart_date": "2026-10-10", "return_date": "2026-10-01"}, "العودة قبل الذهاب"),
    ({"depart_date": "2026-10-10", "return_date": None}, "بدون تاريخ عودة أو مدة"),
])
def test_invalid_departures_dropped(dep, warning):
    n = cat.normalize_extraction({"packages": [_pkg(departures=[{"ref": "d1", "seats_total": None, "notes": None,
                                                                   **dep}])]}, TODAY)
    assert n.packages[0].departures == [] and any(warning in w for w in n.warnings)


def test_package_level_rules():
    n = cat.normalize_extraction({"packages": [
        _pkg(title=" "), _pkg(title="حج VIP", kind="luxury", aliases=["حج VIP", "الحج الملكي", "الحج الملكي", "x"],
                              hotels=[{"city": "riyadh", "hotel_name": "فندق", "stars": 9, "distance_note": None,
                                       "nights": 3}, {"city": "makkah", "hotel_name": "", "stars": 5}]),
    ], "notes": None}, TODAY)
    assert len(n.packages) == 1
    p = n.packages[0]
    assert p.kind == "other" and p.aliases == ["الحج الملكي"]
    assert [(h.city, h.stars) for h in p.hotels] == [("other", None)]
    assert any("بدون اسم" in w for w in n.warnings) and any("لا يوجد سعر للبالغين" in w for w in n.warnings)


def test_limits_empty_and_warning_cap():
    many = cat.normalize_extraction({"packages": [_pkg(title=f"برنامج {i}") for i in range(25)]}, TODAY)
    assert len(many.packages) == cat.MAX_PACKAGES and "حُفظ أول 20" in many.warnings[0]
    noisy = cat.normalize_extraction({"packages": [_pkg(prices=[_price("?") for _ in range(60)])]}, TODAY)
    assert len(noisy.warnings) == cat.MAX_WARNINGS + 1 and noisy.warnings[-1] == "… وتحذيرات أخرى"
    empty = cat.normalize_extraction({"packages": "garbage"}, TODAY)
    assert empty.packages == [] and "لم نجد برامج" in empty.warnings[-1]


# ------------------------------------------------------------------ persistence + handler
class DraftDB:
    def __init__(self, import_row=None):
        self.import_row, self.executed = import_row, []

    async def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        if stmt is aq.IMPORT_FOR_JOB:
            return FakeResult([self.import_row] if self.import_row else [])
        if stmt in (aq.INSERT_DRAFT_PACKAGE, aq.INSERT_PACKAGE_DEPARTURE):
            return FakeResult(scalar=uuid4())
        return FakeResult()

    def calls_to(self, stmt):
        return [p for s, p in self.executed if s is stmt]


def test_insert_drafts_passes_native_types_and_maps_departures():
    """asyncpg يستنتج نوع المعامل من CAST: التاريخ كـ date والمبلغ كـ Decimal (خطأ وُجد في التشغيل الحي)."""
    n = cat.normalize_extraction(catalog_extraction(), date.today())
    db = DraftDB()
    import_id = uuid4()
    ids = run(cat.insert_drafts(db, import_id, n.packages))
    assert len(ids) == 2
    deps = db.calls_to(aq.INSERT_PACKAGE_DEPARTURE)
    assert deps and all(isinstance(d["depart_date"], date) and isinstance(d["return_date"], date) for d in deps)
    prices = db.calls_to(aq.INSERT_PACKAGE_PRICE)
    assert len(prices) == 5 and all(isinstance(p["amount"], Decimal) for p in prices)
    assert all(p["departure_id"] is None for p in prices)
    pkg = db.calls_to(aq.INSERT_DRAFT_PACKAGE)[0]
    assert pkg["source_import_id"] == import_id and json.loads(pkg["includes"])[0] == "تذكرة الطيران"
    assert len(db.calls_to(aq.INSERT_PACKAGE_HOTEL)) == 2 and len(db.calls_to(aq.INSERT_PACKAGE_ALIAS)) == 1


class FakeExtractor:
    model = "fake-vision"

    def __init__(self, data):
        self.data, self.calls = data, []

    async def extract(self, **kw):
        self.calls.append(kw)
        return StructuredResult(data=self.data, usage=Usage(1200, 800), model=self.model)


def _handler_env(monkeypatch, import_row, extractor):
    db = DraftDB(import_row)

    @asynccontextmanager
    async def session(tenant_id, user_id=None):
        yield db

    monkeypatch.setattr(cat, "tenant_session", session)
    deps = AIDeps(settings=get_settings(), graph=None, transcriber=None, extractor=extractor, embedder=None)
    return db, deps, Job(id=uuid4(), tenant_id=uuid4(), kind="catalog_extract", attempts=1,
                         catalog_import_id=uuid4())


def test_handler_extracts_drafts_and_finishes_import(monkeypatch):
    ex = FakeExtractor(catalog_extraction())
    db, deps, job = _handler_env(monkeypatch, {"id": "I", "status": "pending", "mime_type": "image/png",
                                               "file_data": memoryview(PNG)}, ex)
    result = run(cat.CatalogExtractHandler().run(deps, job))
    call = ex.calls[0]
    assert call["document"] == PNG and call["mime_type"] == "image/png" and call["schema"] is cat.CATALOG_SCHEMA
    assert "لا تخمّن" in call["system"]
    fin = db.calls_to(aq.FINISH_IMPORT)[0]
    assert fin["status"] == "done" and fin["packages_created"] == 2 and "USD" in fin["warnings"]
    assert db.calls_to(aq.MARK_IMPORT_PROCESSING) and result.input_tokens == 1200 and result.model == "fake-vision"


def test_handler_skips_processed_and_reports_failure(monkeypatch):
    ex = FakeExtractor({})
    db, deps, job = _handler_env(monkeypatch, {"id": "I", "status": "done", "mime_type": "image/png",
                                               "file_data": None}, ex)
    assert run(cat.CatalogExtractHandler().run(deps, job)).note == "already processed" and ex.calls == []

    db, deps, job = _handler_env(monkeypatch, {"id": "I", "status": "pending", "mime_type": "image/png",
                                               "file_data": PNG}, None)
    with pytest.raises(PermanentJobError):
        run(cat.CatalogExtractHandler().run(deps, job))
    run(cat.CatalogExtractHandler().on_final_failure(deps, job, "boom"))
    fin = db.calls_to(aq.FINISH_IMPORT)[-1]
    assert fin["status"] == "failed" and fin["error"] == cat.IMPORT_FAILED_MESSAGE and "boom" not in fin["error"]
