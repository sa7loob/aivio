"""Channel-agnostic message contracts.

كل قناة (WhatsApp / Messenger / Instagram / TikTok لاحقاً) تحوّل رسائلها إلى هذه الصيغ،
وباقي النظام (Worker, Agent) لا يعرف شيئاً عن تفاصيل أي منصة.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Channel = Literal["messenger", "instagram", "whatsapp", "tiktok"]
MessageType = Literal[
    "text", "image", "audio", "video", "document", "location",
    "interactive", "template", "reaction", "other",
]


class InboundMessage(BaseModel):
    channel: Channel
    account_external_id: str          # phone_number_id / page_id / ig_account_id
    user_external_id: str             # wa_id / PSID / IGSID
    external_message_id: str          # wamid / mid — مفتاح منع التكرار
    type: MessageType
    text: str | None = None
    timestamp: datetime
    user_display_name: str | None = None
    user_phone_e164: str | None = None
    is_echo: bool = False             # رسالة أرسلها موظف من خارج النظام
    raw: dict[str, Any] = Field(default_factory=dict)


class StatusUpdate(BaseModel):
    channel: Channel
    account_external_id: str
    external_message_id: str
    status: str                       # sent / delivered / read / failed
    recipient_id: str | None = None
    timestamp: datetime | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)


class ParsedWebhook(BaseModel):
    messages: list[InboundMessage] = Field(default_factory=list)
    statuses: list[StatusUpdate] = Field(default_factory=list)
    skipped: int = 0                  # عناصر مشوّهة أو غير مدعومة (تُسجَّل ولا تُسقط الحدث)


class SendResult(BaseModel):
    external_message_id: str


class ChannelSendError(Exception):
    """فشل الإرسال. retryable يحدد هل يعيد الـ worker المحاولة لاحقاً."""

    def __init__(self, message: str, *, retryable: bool, code: int | str | None = None,
                 http_status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code
        self.http_status = http_status

    def __str__(self) -> str:
        return f"{self.args[0]} (code={self.code}, http={self.http_status}, retryable={self.retryable})"


class ChannelSender(Protocol):
    async def send_text(self, *, account_external_id: str, access_token: str,
                        to: str, body: str) -> SendResult: ...

    async def send_template(self, *, account_external_id: str, access_token: str, to: str,
                            name: str, language_code: str,
                            body_params: list[str] | None = None) -> SendResult: ...
