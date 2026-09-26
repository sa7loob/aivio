"""OpenAI adapter (official SDK) for chat, embeddings, transcription and document extraction.

الـ SDK يعيد المحاولة داخلياً (max_retries) على 429/5xx/timeouts.
عميل الـ SDK يُمرَّر من الخارج (dependency injection) لتسهيل الاختبار.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.llm.base import (
    ChatMessage,
    LLMError,
    LLMResponse,
    StructuredResult,
    ToolCall,
    ToolSpec,
    Transcript,
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


def _token_usage(usage: Any) -> Usage:
    """usage من التفريغ: tokens (gpt-4o-transcribe) أو duration (whisper-1) أو غير موجود."""
    if usage is None or getattr(usage, "type", "tokens") != "tokens":
        return Usage()
    return Usage(input_tokens=getattr(usage, "input_tokens", 0) or 0,
                 output_tokens=getattr(usage, "output_tokens", 0) or 0)


class OpenAITranscriptionClient:
    def __init__(self, sdk_client: Any, *, model: str) -> None:
        self._sdk = sdk_client
        self.model = model

    async def transcribe(self, *, audio_path: str, prompt: str | None,
                         language: str | None) -> Transcript:
        kwargs: dict[str, Any] = {"model": self.model, "response_format": "json"}
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt
        try:
            # اسم الملف (امتداده) يحدد الصيغة عند OpenAI
            with open(audio_path, "rb") as f:
                resp = await self._sdk.audio.transcriptions.create(file=f, **kwargs)
        except Exception as exc:  # noqa: BLE001 — أخطاء الـ SDK بعد إعادة المحاولات
            raise LLMError(f"transcription failed: {type(exc).__name__}: {exc}") from exc
        return Transcript(text=(getattr(resp, "text", "") or "").strip(),
                          usage=_token_usage(getattr(resp, "usage", None)), model=self.model)


class OpenAIDocumentExtractor:
    """صورة (image_url) أو PDF (جزء file) + Structured Outputs (json_schema strict)."""

    def __init__(self, sdk_client: Any, *, model: str, max_output_tokens: int,
                 temperature: float | None = 0) -> None:
        self._sdk = sdk_client
        self.model = model
        self._max_tokens = max_output_tokens
        self._temperature = temperature

    @staticmethod
    def document_part(document: bytes, mime_type: str) -> dict[str, Any]:
        b64 = base64.b64encode(document).decode()
        if mime_type == "application/pdf":
            return {"type": "file",
                    "file": {"filename": "brochure.pdf", "file_data": f"data:application/pdf;base64,{b64}"}}
        return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}", "detail": "high"}}

    async def extract(self, *, system: str, instruction: str, document: bytes, mime_type: str,
                      schema_name: str, schema: dict[str, Any]) -> StructuredResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [{"type": "text", "text": instruction},
                                             self.document_part(document, mime_type)]},
            ],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
            "max_completion_tokens": self._max_tokens,
        }
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        try:
            resp = await self._sdk.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"extraction failed: {type(exc).__name__}: {exc}") from exc

        choice = resp.choices[0]
        refusal = getattr(choice.message, "refusal", None)
        if refusal:
            raise LLMError(f"extraction refused: {refusal}")
        if choice.finish_reason == "length":
            raise LLMError("extraction truncated (max_completion_tokens)")
        try:
            data = json.loads(choice.message.content or "")
        except ValueError as exc:
            raise LLMError("extraction returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("extraction returned a non-object")
        usage = Usage(input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
                      output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0)
        return StructuredResult(data=data, usage=usage, model=getattr(resp, "model", self.model))
