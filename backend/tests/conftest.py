import json
import os
import pathlib

import pytest
from cryptography.fernet import Fernet

# قيم اختبار فقط — يجب ضبطها قبل استيراد app.*
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://app_user:x@127.0.0.1:5432/agentdb")
os.environ.setdefault("META_APP_SECRET", "test-app-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "test-verify-token")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> dict:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return _load
