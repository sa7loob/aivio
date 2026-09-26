"""Encryption for channel access tokens stored in channel_accounts.access_token_enc."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class TokenDecryptionError(RuntimeError):
    pass


def _fernet() -> Fernet:
    return Fernet(get_settings().token_encryption_key.get_secret_value().encode())


def encrypt_token(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode())


def decrypt_token(blob: bytes | memoryview | None) -> str:
    if blob is None:
        raise TokenDecryptionError("channel account has no access token")
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken as exc:  # مفتاح خاطئ أو بيانات تالفة
        raise TokenDecryptionError("cannot decrypt channel access token") from exc
