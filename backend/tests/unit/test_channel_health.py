import asyncio
import time

from app.channels.meta_graph import MetaGraphError
from app.core.config import get_settings
from app.core.crypto import encrypt_token
from app.worker.health import check_one


class G:
    def __init__(self, fail_code=None, debug=None):
        self.fail_code, self.debug, self.calls = fail_code, debug, []

    async def get_phone_number(self, pid, token):
        self.calls.append(("phone", pid))
        if self.fail_code:
            raise MetaGraphError("boom", retryable=False, code=self.fail_code)
        return {}

    async def get_page(self, pid, token):
        self.calls.append(("page", pid))
        return {}

    async def debug_token(self, token, app_token):
        return self.debug or {"is_valid": True, "expires_at": 0}


def _check(graph, channel="whatsapp", config=None, app_id=None, token=True):
    settings = get_settings().model_copy(update={"meta_app_id": app_id})
    return asyncio.run(check_one(graph, settings, channel, "EXT", encrypt_token("T") if token else None,
                                 config or {}))


def test_ok_and_invalid_token():
    assert _check(G()) == ("active", None)
    status, err = _check(G(fail_code=190))
    assert status == "needs_reauth" and err.startswith("190")


def test_transient_errors_do_not_disconnect():
    status, err = _check(G(fail_code=2))
    assert status == "active" and "check failed" in err


def test_instagram_checks_linked_page_and_debug_token():
    g = G(debug={"is_valid": False})
    assert _check(g, channel="instagram", config={"page_id": "P1"}, app_id="APP")[0] == "needs_reauth"
    assert g.calls == [("page", "P1")]
    soon = int(time.time()) + 3600
    status, err = _check(G(debug={"is_valid": True, "expires_at": soon}), app_id="APP")
    assert status == "active" and "expires soon" in err


def test_undecryptable_token():
    assert _check(G(), token=False)[0] == "needs_reauth"
