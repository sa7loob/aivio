import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from app.agent.history import build_history
from app.agent.knowledge_ingest import MAX_CHUNK_CHARS, chunk_entries
from app.agent.prompts import build_system_prompt
from app.agent.types import CustomerProfile, TenantProfile
from app.llm.base import ChatMessage, LLMError, ToolCall, ToolSpec
from app.llm.openai_client import OpenAIChatClient, OpenAIEmbeddingClient


# ------------------------------------------------------------------ OpenAI adapter
class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.kwargs = None
        self.response, self.error = response, error

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def _sdk(completions=None, embeddings=None):
    return NS(chat=NS(completions=completions), embeddings=embeddings)


def test_openai_request_and_response_mapping():
    resp = NS(model="gpt-x", usage=NS(prompt_tokens=50, completion_tokens=7), choices=[NS(
        finish_reason="tool_calls",
        message=NS(content=None, tool_calls=[
            NS(id="call_1", function=NS(name="search_packages", arguments='{"query":"المولد"}'))]))])
    comp = FakeCompletions(resp)
    client = OpenAIChatClient(_sdk(comp), model="gpt-x", temperature=None, max_output_tokens=300)
    history = [
        ChatMessage("user", "قداش؟"),
        ChatMessage("assistant", None, tool_calls=(ToolCall("call_0", "t", "{}"),)),
        ChatMessage("tool", '{"ok":1}', tool_call_id="call_0"),
    ]
    out = asyncio.run(client.complete(system="SYS", messages=history,
                                      tools=[ToolSpec("t", "d", {"type": "object"})]))

    k = comp.kwargs
    assert k["messages"][0] == {"role": "system", "content": "SYS"}
    assert k["messages"][2]["tool_calls"][0]["function"]["name"] == "t"
    assert k["messages"][3] == {"role": "tool", "tool_call_id": "call_0", "content": '{"ok":1}'}
    assert k["tools"][0]["type"] == "function" and k["tool_choice"] == "auto"
    assert "temperature" not in k and k["max_completion_tokens"] == 300
    assert out.tool_calls == (ToolCall("call_1", "search_packages", '{"query":"المولد"}'),)
    assert (out.usage.input_tokens, out.usage.output_tokens) == (50, 7)


def test_openai_errors_become_llm_error():
    client = OpenAIChatClient(_sdk(FakeCompletions(error=RuntimeError("429"))), model="m",
                              temperature=0.3, max_output_tokens=10)
    with pytest.raises(LLMError):
        asyncio.run(client.complete(system="s", messages=[], tools=[]))


def test_embeddings_order_and_dimension_check():
    class Emb:
        def __init__(self, dim):
            self.dim = dim

        async def create(self, **kw):
            assert kw["dimensions"] == 3
            return NS(data=[NS(index=1, embedding=[1.0] * self.dim), NS(index=0, embedding=[0.0] * self.dim)])

    ok = OpenAIEmbeddingClient(_sdk(embeddings=Emb(3)), model="e", dimensions=3)
    assert asyncio.run(ok.embed(["a", "b"])) == [[0.0] * 3, [1.0] * 3]
    bad = OpenAIEmbeddingClient(_sdk(embeddings=Emb(4)), model="e", dimensions=3)
    with pytest.raises(LLMError):
        asyncio.run(bad.embed(["a"]))


# ------------------------------------------------------------------ prompt
def _profiles(phone):
    t = TenantProfile(tenant_id=uuid4(), name="وكالة الريان",
                      settings={"working_hours": "السبت للخميس", "assistant_name": "مساعد الريان"})
    c = CustomerProfile(contact_id=uuid4(), channel="whatsapp", display_name="Ali", phone_e164=phone)
    return t, c


def test_prompt_contains_context_and_phone_rule():
    t, c = _profiles("+218913334444")
    p = build_system_prompt(t, c, datetime(2026, 9, 25, 22, 30, tzinfo=timezone.utc))
    assert "وكالة الريان" in p and "مساعد الريان" in p and "واتساب" in p
    assert "السبت للخميس" in p
    assert "2026-09-26" in p and "السبت" in p          # 00:30 بتوقيت طرابلس = السبت
    assert "لا تطلبه" in p
    t, c = _profiles(None)
    assert "اطلب رقم هاتف" in build_system_prompt(t, c, datetime.now(timezone.utc))


# ------------------------------------------------------------------ history
def test_history_from_rows():
    rows_newest_first = [
        {"direction": "inbound", "msg_type": "text", "text_content": "قداش؟"},
        {"direction": "inbound", "msg_type": "reaction", "text_content": "👍"},
        {"direction": "inbound", "msg_type": "image", "text_content": None},
        {"direction": "outbound", "msg_type": "text", "text_content": "مرحبا بيك"},
        {"direction": "outbound", "msg_type": "text", "text_content": "رسالة قديمة من البوت"},
    ]
    h = build_history(rows_newest_first)
    # الأقدم أولاً، بدون التفاعلات، وبدون ردود البوت في البداية
    assert [(m.role, m.content) for m in h] == [("user", "[صورة]"), ("user", "قداش؟")]


# ------------------------------------------------------------------ knowledge chunking
def test_chunking_merges_and_splits():
    entries = [
        {"title": "قصير", "content": "فقرة 1\n\nفقرة 2", "source_type": "faq"},
        {"title": "طويل", "content": ("كلمة " * 400), "source_type": "policy", "package_code": "X"},
    ]
    chunks = chunk_entries(entries)
    assert chunks[0].content == "فقرة 1\nفقرة 2"
    long_parts = [c for c in chunks if c.title == "طويل"]
    assert len(long_parts) >= 2 and all(len(c.content) <= MAX_CHUNK_CHARS for c in long_parts)
    assert long_parts[0].package_code == "X" and long_parts[0].embed_input.startswith("طويل\n")
