from app.identity.security import (
    hash_password,
    needs_rehash,
    new_token,
    token_hash,
    verify_password,
)


def test_password_roundtrip_and_salting():
    h1, h2 = hash_password("كلمة-سر-قوية-123"), hash_password("كلمة-سر-قوية-123")
    assert h1 != h2 and h1.startswith("scrypt$")
    assert verify_password("كلمة-سر-قوية-123", h1)
    assert not verify_password("كلمة-سر-قوية-124", h1)
    assert not needs_rehash(h1)


def test_malformed_or_foreign_hashes_are_rejected():
    assert not verify_password("x", "")
    assert not verify_password("x", "bcrypt$abc")
    assert not verify_password("x", "scrypt$notanumber$8$1$AAAA$BBBB")
    assert needs_rehash("scrypt$1024$8$1$AAAA$BBBB")


def test_tokens():
    a, b = new_token("ses"), new_token("ses")
    assert a != b and a.startswith("ses_") and len(a) > 40
    assert token_hash(a) == token_hash(a) and token_hash(a) != token_hash(b)
    assert a not in token_hash(a)
