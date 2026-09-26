import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel

from app.agent import tools as tools_mod
from app.agent.core import FALLBACK_REPLY, Agent, clean_reply
from app.agent.tools import Tool, ToolRegistry, ToolUserError
from app.agent.types import AgentInput, TenantProfile
from app.llm.base import ChatMessage, LLMError
from tests.unit.fakes import ScriptedLLM, make_ctx, text_reply, tool_reply


class QArgs(BaseModel):
    query: str


async def _echo(ctx, a):
    return {"echo": a.query}


async def _user_error(ctx, a):
    raise ToolUserError("رقم غير صحيح")


async def _slow(ctx, a):
    await asyncio.sleep(1)
    return {}


async def _set_lead(ctx, a):
    ctx.lead_id = uuid4()
    return {"ok": True}


REG = ToolRegistry([
    Tool("search_packages", "search", QArgs, _echo),
    Tool("bad", "fails", QArgs, _user_error),
    Tool("slow", "slow", QArgs, _slow),
    Tool("create_lead", "lead", QArgs, _set_lead),
])


def _input(ctx, turns=("قداش عمرة المولد؟",)):
    return AgentInput(
        tenant=TenantProfile(tenant_id=ctx.tenant_id, name="وكالة تجريبية"),
        customer=ctx.customer, conversation_id=ctx.conversation_id,
        history=[ChatMessage("user", t) for t in turns],
        now=datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
    )


def _run(agent, ctx, **kw):
    return asyncio.run(agent.run(_input(ctx, **kw), ctx))


def test_tool_then_final_reply():
    llm = ScriptedLLM([tool_reply(("search_packages", '{"query": "المولد"}')),
                       text_reply("**السعر** 6500 دينار للرباعي")])
    ctx = make_ctx()
    out = _run(Agent(llm, REG), ctx)

    assert out.status == "ok"
    assert out.reply_text == "*السعر* 6500 دينار للرباعي"
    assert out.iterations == 2
    assert (out.input_tokens, out.output_tokens) == (200, 30)
    assert [t.name for t in out.traces] == ["search_packages"] and out.traces[0].ok
    second_call_msgs = llm.calls[1]["messages"]
    assert second_call_msgs[-2].role == "assistant" and second_call_msgs[-2].tool_calls
    assert second_call_msgs[-1].role == "tool" and second_call_msgs[-1].tool_call_id == "c0"
    assert '"echo": "المولد"' in second_call_msgs[-1].content
    assert "وكالة تجريبية" in llm.calls[0]["system"]
    assert {s.name for s in llm.calls[0]["tools"]} == {"search_packages", "bad", "slow", "create_lead"}


def test_parallel_tool_calls_keep_order():
    llm = ScriptedLLM([tool_reply(("search_packages", '{"query": "a"}'),
                                  ("search_packages", '{"query": "b"}')),
                       text_reply("تمام")])
    out = _run(Agent(llm, REG), make_ctx())
    tool_msgs = [m for m in llm.calls[1]["messages"] if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ["c0", "c1"]
    assert '"a"' in tool_msgs[0].content and '"b"' in tool_msgs[1].content
    assert len(out.traces) == 2


def test_invalid_arguments_are_returned_to_model():
    llm = ScriptedLLM([tool_reply(("search_packages", "{not json")),
                       tool_reply(("search_packages", '{"wrong": 1}')),
                       tool_reply(("nope", "{}")),
                       text_reply("معليش")])
    out = _run(Agent(llm, REG), make_ctx())
    assert out.status == "ok"
    assert [t.error for t in out.traces] == ["invalid_arguments", "invalid_arguments", "unknown_tool"]
    assert "invalid_arguments" in llm.calls[1]["messages"][-1].content


def test_user_error_is_explained_to_model():
    llm = ScriptedLLM([tool_reply(("bad", '{"query": "x"}')), text_reply("عطيني رقم صحيح")])
    out = _run(Agent(llm, REG), make_ctx())
    assert out.traces[0].error == "user_error"
    assert "رقم غير صحيح" in llm.calls[1]["messages"][-1].content


def test_tool_timeout(monkeypatch):
    monkeypatch.setattr(tools_mod, "TOOL_TIMEOUT_SECONDS", 0.05)
    llm = ScriptedLLM([tool_reply(("slow", '{"query": "x"}')), text_reply("تمام")])
    out = _run(Agent(llm, REG), make_ctx())
    assert out.traces[0].error == "timeout" and out.status == "ok"


def test_iteration_limit_falls_back_to_human():
    llm = ScriptedLLM([tool_reply(("search_packages", '{"query": "x"}'))] * 3)
    out = _run(Agent(llm, REG, max_iterations=3), make_ctx())
    assert out.status == "fallback"
    assert out.reply_text == FALLBACK_REPLY and out.needs_human
    assert out.iterations == 3


def test_llm_failure_first_call():
    llm = ScriptedLLM([LLMError("rate limited")])
    out = _run(Agent(llm, REG), make_ctx())
    assert out.status == "error" and out.needs_human and out.reply_text == FALLBACK_REPLY
    assert "rate limited" in out.error


def test_empty_final_reply_is_error():
    llm = ScriptedLLM([text_reply("   ")])
    out = _run(Agent(llm, REG), make_ctx())
    assert out.needs_human and out.reply_text == FALLBACK_REPLY


def test_side_effects_propagate():
    llm = ScriptedLLM([tool_reply(("create_lead", '{"query": "x"}')), text_reply("تسجل طلبك")])
    out = _run(Agent(llm, REG), make_ctx())
    assert out.lead_id is not None


def test_clean_reply_for_whatsapp():
    raw = "## العروض\n- **رباعي**: 6500\n- ثلاثي: 7200\n\n\n\nباهي؟"
    assert clean_reply(raw) == "العروض\n• *رباعي*: 6500\n• ثلاثي: 7200\n\nباهي؟"
    assert clean_reply("  ") is None
    long = "سطر\n" * 1000
    assert len(clean_reply(long)) <= 1502
