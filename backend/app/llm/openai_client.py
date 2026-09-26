"""OpenAI adapter (official SDK) for chat + embeddings.

الـ SDK يعيد المحاولة داخلياً (max_retries) على 429/5xx/timeouts.
عميل الـ SDK يُمرَّر من الخارج (dependency injection) لتسهيل الاختبار.
"""
from __future__ import annotations

import logging
from typing import Any

from app.llm.base import (
    ChatMessage,
    LLMError,
    LLMResponse,
    ToolCall,
    ToolSpec,
    Usage,
)

log = logging.getLogger(__name__)


def create_openai_sdk_client(api_key: str, *, timeout: float, max_retries: int) -> Any:
    from openai import AsyncOpenAI  # استيراد متأخر: الاختبارات لا تحتاج الحزمة
    return AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)


def _to_openai_messages(system: str, messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        elif m.role == "assistant" and m.tool_calls:
            out.append({
                "role": "assistant",
                "content": m.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in m.tool_calls
                ],
            })
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


def _to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [{"type": "function",
             "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools]


class OpenAIChatClient:
    def __init__(self, sdk_client: Any, *, model: str, temperature: float | None,
                 max_output_tokens: int) -> None:
        self._sdk = sdk_client
        self.model = model
        self._temperature = temperature
        self._max_tokens = max_output_tokens

    async def complete(self, *, system: str, messages: list[ChatMessage],
                       tools: list[ToolSpec]) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(system, messages),
            # max_completion_tokens (الاسم الأحدث) يعمل مع gpt-4o والموديلات الأحدث
            "max_completion_tokens": self._max_tokens,
        }
        if self._temperature is not None:  # بعض موديلات reasoning لا تقبل temperature
            kwargs["temperature"] = self._temperature
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        try:
            resp = await self._sdk.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — أخطاء الـ SDK بعد إعادة المحاولات
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc

        choice = resp.choices[0]
        msg = choice.message
        calls = tuple(
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        )
        usage = Usage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        if choice.finish_reason == "length" and not calls:
            log.warning("llm: reply truncated at max_tokens")
        return LLMResponse(text=msg.content, tool_calls=calls, usage=usage,
                           model=getattr(resp, "model", self.model))


class OpenAIEmbeddingClient:
    def __init__(self, sdk_client: Any, *, model: str, dimensions: int) -> None:
        self._sdk = sdk_client
        self.model = model
        self._dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self._sdk.embeddings.create(
                model=self.model, input=texts, dimensions=self._dimensions)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"embeddings failed: {type(exc).__name__}: {exc}") from exc
        vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        if any(len(v) != self._dimensions for v in vectors):
            raise LLMError("embedding dimension mismatch with knowledge_chunks schema")
        return vectors
