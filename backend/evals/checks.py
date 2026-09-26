"""Deterministic checks for one eval case (pure functions — unit-testable)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# علامات فصحى رسمية أو لهجات أخرى: تحذير أسلوب (لا يُفشل الحالة)
STYLE_MARKERS = ["سوف ", "لديكم", "لدينا", "نودّ", "نود إعلامكم", "ايش", "ليش", "عايز", "كيفك"]


@dataclass
class CaseResult:
    id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    judge: dict[str, Any] | None = None
    tokens: int = 0
    latency_ms: int = 0


def check_case(expect: dict[str, Any], calls: list[tuple[str, dict[str, Any] | None, bool]],
               final_reply: str, lead: dict[str, Any] | None = None,
               staff_notifications: int = 0) -> tuple[list[str], list[str]]:
    """calls: [(tool_name, args, ok)] عبر كل أدوار المحادثة بالترتيب."""
    failures: list[str] = []
    names = [c[0] for c in calls]

    for t in expect.get("tools_all", []):
        if t not in names:
            failures.append(f"expected tool {t} was not called")
    any_of = expect.get("tools_any", [])
    if any_of and not any(t in names for t in any_of):
        failures.append(f"expected one of {any_of}")
    for t in expect.get("tools_none", []):
        if t in names:
            failures.append(f"forbidden tool {t} was called")

    for tool, wanted in (expect.get("tool_args") or {}).items():
        ok_calls = [c for c in calls if c[0] == tool and c[2]]
        if not ok_calls:
            failures.append(f"no successful {tool} call to check args")
            continue
        args = ok_calls[-1][1] or {}
        for k, v in wanted.items():
            if args.get(k) != v:
                failures.append(f"{tool}.{k}: expected {v!r}, got {args.get(k)!r}")

    reply = final_reply or ""
    wanted_any = expect.get("reply_contains_any", [])
    if wanted_any and not any(w in reply for w in wanted_any):
        failures.append(f"reply lacks any of {wanted_any}")
    for bad in expect.get("reply_not_contains", []):
        if bad in reply:
            failures.append(f"reply contains forbidden text {bad!r}")

    db = expect.get("db_lead")
    if db:
        if lead is None:
            failures.append("no lead row in database")
        else:
            for k, v in db.items():
                if k == "staff_notified":
                    if bool(staff_notifications) != v:
                        failures.append(f"staff_notified: expected {v}, got {staff_notifications} queued")
                elif lead.get(k) != v:
                    failures.append(f"lead.{k}: expected {v!r}, got {lead.get(k)!r}")

    warnings = [f"style marker {m.strip()!r} in reply" for m in STYLE_MARKERS if m in reply]
    if len(reply) > 900:
        warnings.append(f"long reply ({len(reply)} chars)")
    return failures, warnings
