"""Password hashing (scrypt, stdlib) and opaque tokens.

لماذا scrypt من hashlib؟ خوارزمية memory-hard معتمدة، ولا تحتاج مكتبة إضافية (argon2-cffi).
الصيغة المخزنة: scrypt$N$r$p$<salt_b64>$<hash_b64>  => يمكن رفع N لاحقاً مع needs_rehash().

الجلسات opaque (وليس JWT): التوكن عشوائي، ويُخزَّن hash فقط، والإلغاء فوري من قاعدة البيانات.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P, _DKLEN = 2 ** 15, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 10


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r),
                            p=int(p), maxmem=_MAXMEM, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def needs_rehash(stored: str) -> bool:
    try:
        _, n, r, p, *_ = stored.split("$")
        return (int(n), int(r), int(p)) != (_N, _R, _P)
    except ValueError:
        return True


# hash ثابت لتوحيد زمن الرد عند بريد غير موجود (لا نكشف وجود الحساب بالتوقيت)
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_dummy(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
