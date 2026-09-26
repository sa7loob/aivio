"""Agent input/output and the per-run tool context."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.llm.base import ChatMessage, EmbeddingClient

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[Any]]


@dataclass(frozen=True)
class TenantProfile:
    tenant_id: UUID
    name: str
    timezone: str = "Africa/Tripoli"
    currency: str = "LYD"
    settings: dict[str, Any] = field(default_factory=dict)   # working_hours, address, ...


@dataclass(frozen=True)
class CustomerProfile:
    contact_id: UUID
    channel: str
    display_name: str | None = None
    phone_e164: str | None = None        # معروف مسبقاً في واتساب


@dataclass(frozen=True)
class AgentInput:
    tenant: TenantProfile
    customer: CustomerProfile
    conversation_id: UUID
    history: list[ChatMessage]           # الأقدم أولاً، آخرها رسائل الزبون الجديدة
    now: datetime


@dataclass
class ToolContext:
    """ما تحتاجه الأدوات أثناء تشغيل واحد، + أثر جانبي تسجله الأدوات (lead_id, handoff)."""
    tenant_id: UUID
    conversation_id: UUID
    customer: CustomerProfile
    session_factory: SessionFactory      # tenant_session في الإنتاج، بديل في الاختبارات
    embedder: EmbeddingClient | None
    lead_template_name: str = "new_lead"
    lead_template_language: str = "ar"
    price_stale_days: int = 14
    knowledge_top_k: int = 4
    handoff_pause_hours: int = 12
    # --- يملؤها تنفيذ الأدوات
    lead_id: UUID | None = None
    handed_off: bool = False


@dataclass
class ToolTrace:
    name: str
    args: dict[str, Any] | None
    ok: bool
    ms: int
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "args": self.args, "ok": self.ok, "ms": self.ms, "error": self.error}


@dataclass
class AgentOutput:
    reply_text: str | None
    status: str                          # ok | fallback | error
    model: str
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    traces: list[ToolTrace] = field(default_factory=list)
    lead_id: UUID | None = None
    handed_off: bool = False
    needs_human: bool = False            # فشل => أوقف البوت مؤقتاً ليتدخل الموظف
    error: str | None = None
