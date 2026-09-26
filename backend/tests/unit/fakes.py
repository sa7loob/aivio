"""Test doubles: scripted LLM and an in-memory DB session keyed by SQL statement."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from app.agent.types import CustomerProfile, ToolContext
from app.llm.base import ChatMessage, LLMError, LLMResponse, ToolCall, ToolSpec, Usage


class ScriptedLLM:
    model = "fake-model"

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, messages: list[ChatMessage],
                       tools: list[ToolSpec]) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.script:
            raise LLMError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text_reply(t: str) -> LLMResponse:
    return LLMResponse(text=t, usage=Usage(100, 20), model="fake-model")


def tool_reply(*calls: tuple[str, str], call_id_prefix: str = "c") -> LLMResponse:
    return LLMResponse(text=None, usage=Usage(100, 10), model="fake-model",
                       tool_calls=tuple(ToolCall(f"{call_id_prefix}{i}", n, a)
                                        for i, (n, a) in enumerate(calls)))


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one(self) -> dict[str, Any]:
        assert len(self._rows) == 1, self._rows
        return self._rows[0]

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class FakeDB:
    """responses: {statement_object: FakeResult | callable(params)->FakeResult}"""

    def __init__(self, responses: dict[Any, Any]) -> None:
        self.responses = responses
        self.executed: list[tuple[Any, dict[str, Any] | None]] = []
        self.sessions_opened: list[Any] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.executed.append((stmt, params))
        for key, resp in self.responses.items():
            if key is stmt:
                return resp(params) if callable(resp) else resp
        return FakeResult()

    def factory(self):
        @asynccontextmanager
        async def _session(tenant_id):
            self.sessions_opened.append(tenant_id)
            yield self
        return _session

    def calls_to(self, stmt: Any) -> list[dict[str, Any] | None]:
        return [p for s, p in self.executed if s is stmt]


def make_ctx(db: FakeDB | None = None, phone: str | None = "+218913334444",
             channel: str = "whatsapp") -> ToolContext:
    db = db or FakeDB({})
    return ToolContext(
        tenant_id=uuid4(), conversation_id=uuid4(),
        customer=CustomerProfile(contact_id=uuid4(), channel=channel, display_name="Ali",
                                 phone_e164=phone),
        session_factory=db.factory(), embedder=None,
    )
