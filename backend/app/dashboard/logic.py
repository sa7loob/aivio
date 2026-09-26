"""Pure dashboard logic (no DB): cursors, lead change detection, CSV export, reply window."""
from __future__ import annotations

import base64
import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

WINDOW_24H = timedelta(hours=24)
LEAD_STATUSES = ("new", "contacted", "qualified", "booked", "lost", "spam")

STATUS_AR = {"new": "جديد", "contacted": "تم التواصل", "qualified": "مهتم جداً", "booked": "تم الحجز",
             "lost": "لم يتم", "spam": "غير جاد"}
ROOM_AR = {"quad": "رباعي", "triple": "ثلاثي", "double": "ثنائي", "single": "مفرد", "shared": "مشترك", "na": "-"}
CHANNEL_AR = {"whatsapp": "واتساب", "messenger": "ماسنجر", "instagram": "إنستغرام", "tiktok": "تيك توك",
              "manual": "يدوي"}


# ------------------------------------------------------------------ keyset cursors
def encode_cursor(ts: datetime, row_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{row_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    """cursor خاطئ => ValueError (الـ API يرد 400)."""
    if not cursor:
        return None, None
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    ts, row_id = raw.split("|", 1)
    return datetime.fromisoformat(ts), UUID(row_id)


def next_cursor(rows: list[Any], limit: int, ts_key: str) -> str | None:
    if len(rows) < limit or not rows:
        return None
    last = rows[-1]
    return encode_cursor(last[ts_key], last["id"])


# ------------------------------------------------------------------ search
_PHONE_LIKE = re.compile(r"[\d\s()+\-]{3,}")


def normalize_search(q: str | None) -> str | None:
    """نص البحث في المحادثات والطلبات. رقم بأي صيغة محلية (0913334444، 091 333 4444، 00218...)
    يتحول إلى أرقام فقط بدون 00 أو 0 في البداية => يطابق +218913334444 و wa_id بالبحث الجزئي."""
    text = (q or "").strip()
    if not text:
        return None
    if _PHONE_LIKE.fullmatch(text):
        digits = re.sub(r"\D", "", text)
        if digits.startswith("00"):
            digits = digits[2:]
        digits = digits.lstrip("0")
        return digits or None
    return text


# ------------------------------------------------------------------ reply window
def window_open(last_inbound_at: datetime | None, now: datetime | None = None) -> bool:
    """واتساب/ماسنجر/إنستغرام: رسالة حرة للزبون فقط خلال 24 ساعة من آخر رسالة منه."""
    if last_inbound_at is None:
        return False
    return (now or datetime.now(timezone.utc)) - last_inbound_at < WINDOW_24H


# ------------------------------------------------------------------ leads
@dataclass
class LeadPatch:
    status: str | None = None
    lost_reason: str | None = None
    assigned_to: UUID | None = None
    unassign: bool = False
    notes: str | None = None


class LeadPatchError(ValueError):
    pass


def apply_lead_patch(current: dict[str, Any], patch: LeadPatch) -> tuple[dict[str, Any], list[tuple[str, dict]]]:
    """يعيد (القيم الجديدة، أحداث lead_events). لا شيء يتغير => لا أحداث."""
    new = {"status": current["status"], "lost_reason": current["lost_reason"],
           "assigned_to": current["assigned_to"], "notes": current["notes"]}
    events: list[tuple[str, dict]] = []

    if patch.status is not None and patch.status != current["status"]:
        if patch.status not in LEAD_STATUSES:
            raise LeadPatchError("invalid_status")
        new["status"] = patch.status
        events.append(("status_changed", {"from": current["status"], "to": patch.status}))
    if new["status"] == "lost":
        reason = patch.lost_reason if patch.lost_reason is not None else current["lost_reason"]
        if not reason or not reason.strip():
            raise LeadPatchError("lost_reason_required")
        new["lost_reason"] = reason.strip()
    elif patch.status is not None and patch.status != "lost":
        new["lost_reason"] = None

    if patch.unassign and current["assigned_to"] is not None:
        new["assigned_to"] = None
        events.append(("assigned", {"from": _s(current["assigned_to"]), "to": None}))
    elif patch.assigned_to is not None and patch.assigned_to != current["assigned_to"]:
        new["assigned_to"] = patch.assigned_to
        events.append(("assigned", {"from": _s(current["assigned_to"]), "to": _s(patch.assigned_to)}))

    if patch.notes is not None and patch.notes != (current["notes"] or ""):
        new["notes"] = patch.notes or None
        events.append(("updated", {"field": "notes"}))
    return new, events


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


EXPORT_COLUMNS = [
    ("created_at", "تاريخ الطلب"), ("status", "الحالة"), ("full_name", "الاسم"), ("phone_e164", "الهاتف"),
    ("city", "المدينة"), ("package_title", "البرنامج"), ("depart_date", "موعد الانطلاق"),
    ("adults", "بالغين"), ("children", "أطفال"), ("infants", "رضّع"), ("room_type_pref", "الغرفة"),
    ("preferred_period", "الفترة المفضلة"), ("assigned_name", "الموظف"), ("source_channel", "القناة"),
    ("notes", "ملاحظات"), ("lost_reason", "سبب عدم الإتمام"),
]


def _cell(key: str, value: Any, tz: timezone | Any) -> str:
    if value is None:
        return ""
    if key == "created_at" and isinstance(value, datetime):
        return value.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    if key == "status":
        return STATUS_AR.get(value, value)
    if key == "room_type_pref":
        return ROOM_AR.get(value, value)
    if key == "source_channel":
        return CHANNEL_AR.get(value, value)
    if key == "phone_e164":
        return f"‎{value}"          # LRM: يبقى الرقم سليماً في Excel العربي (بدون تحويله لرقم)
    if isinstance(value, Decimal):
        return format(value, "f")
    text = str(value)
    # حماية من CSV/formula injection عند فتح الملف في Excel
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def leads_to_csv(rows: list[dict[str, Any]], tz: Any = timezone.utc) -> bytes:
    """CSV بعناوين عربية + BOM (يفتح صحيحاً في Excel)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in EXPORT_COLUMNS])
    for r in rows:
        w.writerow([_cell(k, r.get(k), tz) for k, _ in EXPORT_COLUMNS])
    return buf.getvalue().encode("utf-8-sig")
