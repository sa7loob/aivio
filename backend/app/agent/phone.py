"""Phone normalization for Libyan numbers as customers actually type them."""
from __future__ import annotations

import re

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def normalize_phone(raw: str | None, default_country: str = "218") -> str | None:
    """'091-333 4444' / '٠٩١٣٣٣٤٤٤٤' / '00218913334444' / '913334444' -> '+218913334444'.
    يعيد None إذا لم يكن رقماً صالحاً."""
    if not raw:
        return None
    s = raw.translate(_AR_DIGITS)
    s = re.sub(r"[\s\-().]", "", s)
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        return s if _E164.match(s) else None
    if not s.isdigit():
        return None
    if default_country == "218":
        if s.startswith("218") and len(s) == 12:
            s = s[3:]
        if s.startswith("0"):
            s = s[1:]
        # الموبايل الليبي: 9 أرقام تبدأ بـ 9 (091 / 092 / 093 / 094 / 095 ...)
        if len(s) == 9 and s.startswith("9"):
            return f"+218{s}"
        return None
    candidate = f"+{default_country}{s.lstrip('0')}"
    return candidate if _E164.match(candidate) else None
