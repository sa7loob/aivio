"""Meta Graph API calls used for (assisted) channel onboarding and health checks.

نفس الاستدعاءات سيستخدمها الربط الذاتي (Embedded Signup) في المرحلة 5.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.channels.base import ChannelSendError

PAGE_WEBHOOK_FIELDS = "messages,messaging_postbacks,message_echoes,message_reads"


class MetaGraphError(ChannelSendError):
    pass


class MetaGraphClient:
    def __init__(self, http: httpx.AsyncClient, *, base_url: str, api_version: str) -> None:
        self._http = http
        self._base = f"{base_url.rstrip('/')}/{api_version}"

    async def _call(self, method: str, path: str, token: str | None,
                    params: dict[str, Any] | None = None,
                    json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            resp = await self._http.request(method, f"{self._base}/{path.lstrip('/')}",
                                            params=params, json=json_body, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise MetaGraphError(f"network error: {type(exc).__name__}", retryable=True,
                                 code="network") from exc
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error") or {}
            raise MetaGraphError(err.get("message") or resp.text[:200],
                                 retryable=resp.status_code >= 500 or resp.status_code == 429,
                                 code=err.get("code"), http_status=resp.status_code)
        return data

    # ---------------------------------------------------------------- WhatsApp
    async def get_phone_number(self, phone_number_id: str, token: str) -> dict[str, Any]:
        return await self._call("GET", phone_number_id, token, {
            "fields": "display_phone_number,verified_name,quality_rating,code_verification_status,name_status"})

    async def subscribe_waba(self, waba_id: str, token: str) -> dict[str, Any]:
        return await self._call("POST", f"{waba_id}/subscribed_apps", token)

    async def get_media(self, media_id: str, token: str) -> dict[str, Any]:
        """وسائط واتساب الواردة: {url, mime_type, file_size, sha256}. الرابط صالح لدقائق فقط."""
        return await self._call("GET", media_id, token)

    async def download(self, url: str, token: str | None, *, max_bytes: int) -> tuple[bytes, str | None]:
        """تنزيل ملف (رابط وسائط واتساب يحتاج التوكن؛ مرفقات ماسنجر لا).
        يتوقف فور تجاوز max_bytes دون تنزيل الباقي."""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with self._http.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise MetaGraphError(f"media download failed: http {resp.status_code}",
                                         retryable=resp.status_code >= 500 or resp.status_code == 429,
                                         code="media_download", http_status=resp.status_code)
                declared = int(resp.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise MetaGraphError(f"media too large: {declared} bytes", retryable=False, code="too_large")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise MetaGraphError(f"media too large: >{max_bytes} bytes", retryable=False,
                                             code="too_large")
                return bytes(buf), resp.headers.get("content-type")
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise MetaGraphError(f"network error: {type(exc).__name__}", retryable=True, code="network") from exc

    # ---------------------------------------------------------------- Messenger / Instagram
    async def get_page(self, page_id: str, token: str) -> dict[str, Any]:
        return await self._call("GET", page_id, token, {
            "fields": "id,name,instagram_business_account{id,username}"})

    async def subscribe_page(self, page_id: str, token: str) -> dict[str, Any]:
        return await self._call("POST", f"{page_id}/subscribed_apps", token,
                                {"subscribed_fields": PAGE_WEBHOOK_FIELDS})

    async def register_phone(self, phone_number_id: str, token: str, pin: str) -> dict[str, Any]:
        """تسجيل الرقم في Cloud API مع PIN التحقق بخطوتين (6 أرقام)."""
        return await self._call("POST", f"{phone_number_id}/register", token,
                                json_body={"messaging_product": "whatsapp", "pin": pin})

    async def get_me(self, user_token: str) -> dict[str, Any]:
        """معرّف مستخدم فيسبوك الخاص بالتطبيق: يربط القنوات بمن ربطها (Deauthorize / Data Deletion)."""
        return await self._call("GET", "me", user_token, {"fields": "id"})

    async def list_pages(self, user_token: str) -> list[dict[str, Any]]:
        data = await self._call("GET", "me/accounts", user_token, {
            "fields": "id,name,access_token,instagram_business_account{id,username}", "limit": 100})
        return data.get("data") or []

    # ---------------------------------------------------------------- OAuth (بدون Bearer: اعتماد التطبيق)
    async def exchange_code(self, code: str, *, app_id: str, app_secret: str,
                            redirect_uri: str | None = None) -> dict[str, Any]:
        """code (من Embedded Signup / Facebook Login) => access token. صلاحية الـ code قصيرة جداً."""
        params = {"client_id": app_id, "client_secret": app_secret, "code": code}
        if redirect_uri is not None:
            params["redirect_uri"] = redirect_uri
        return await self._call("GET", "oauth/access_token", None, params)

    async def exchange_long_lived(self, user_token: str, *, app_id: str, app_secret: str) -> dict[str, Any]:
        """توكن مستخدم قصير => طويل (~60 يوماً). توكنات الصفحات المشتقة منه لا تنتهي بالوقت."""
        return await self._call("GET", "oauth/access_token", None, {
            "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": user_token})

    # ---------------------------------------------------------------- tokens
    async def debug_token(self, token: str, app_token: str) -> dict[str, Any]:
        """app_token = '<app_id>|<app_secret>'. يعيد is_valid, expires_at, scopes."""
        data = await self._call("GET", "debug_token", app_token, {"input_token": token})
        return data.get("data") or {}
