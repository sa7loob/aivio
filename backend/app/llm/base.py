"""Provider-neutral LLM contracts.

الـ Agent لا يعرف OpenAI. أي مزوّد (OpenAI / Anthropic / موديل محلي) = adapter واحد
يحوّل هذه الأنواع من وإلى صيغته.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str            # JSON نصي كما أعاده الموديل (قد يكون غير صالح)


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()     # role=assistant فقط
    tool_call_id: str | None = None           # role=tool فقط


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]                # JSON Schema


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class LLMError(Exception):
    """فشل الموديل بعد استنفاد إعادة المحاولات الداخلية."""


class LLMClient(Protocol):
    model: str

    async def complete(self, *, system: str, messages: list[ChatMessage],
                       tools: list[ToolSpec]) -> LLMResponse: ...


class EmbeddingClient(Protocol):
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
