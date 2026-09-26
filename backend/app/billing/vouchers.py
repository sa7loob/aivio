"""Platform voucher codes: generation, validation, hashing.

الرمز: 16 رقماً (15 عشوائية + رقم تحقق Luhn) يُطبع هكذا: 4821-0937-5512-6604
  - Luhn يكشف خطأ إدخال رقم واحد أو تبديل رقمين متجاورين قبل أي استعلام (ولا يُحسب محاولة فاشلة).
  - يُخزَّن HMAC-SHA256(pepper, code) فقط؛ من يسرّب قاعدة البيانات لا يستطيع استخدام القسائم.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

CODE_LENGTH = 16
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _luhn_checksum(digits: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10


def luhn_check_digit(body: str) -> str:
    return str((10 - _luhn_checksum(body + "0")) % 10)


def luhn_valid(code: str) -> bool:
    return code.isdigit() and _luhn_checksum(code) == 0


def generate_code() -> str:
    body = "".join(str(secrets.randbelow(10)) for _ in range(CODE_LENGTH - 1))
    return body + luhn_check_digit(body)


def format_code(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def normalize_code(raw: str) -> str | None:
    """يقبل الأرقام العربية والمسافات والشرطات كما يكتبها الزبون. None = صيغة خاطئة."""
    code = re.sub(r"[\s\-]", "", (raw or "").translate(_AR_DIGITS))
    if len(code) != CODE_LENGTH or not luhn_valid(code):
        return None
    return code


def hash_code(code: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), code.encode(), hashlib.sha256).hexdigest()
