"""The agent loop: LLM <-> tools until a final text reply.

    history + system prompt
        -> LLM
        -> tool calls?  نعم: تنفيذ الأدوات (بالتوازي) وإعادة النتائج للموديل، ثم تكرار
                         لا : رد نهائي
    بحد أقصى agent_max_tool_iterations دورة.

لا يلمس قاعدة البيانات مباشرة: كل وصول يمر عبر الأدوات (ToolContext.session_factory).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from app.agent.prompts import build_system_prompt
from app.agent.tools import ToolRegistry
from app.agent.types import AgentInput, AgentOutput, ToolContext
from app.llm.base import ChatMessage, LLMClient, LLMError

log = logging.getLogger(__name__)

FALLBACK_REPLY = ("معليش، صار عندنا تأخير بسيط في النظام. "
                  "موظف من الوكالة حيتواصل معاك في أقرب وقت إن شاء الله.")
MAX_REPLY_CHARS = 1500


def clean_reply(text: str | None) -> str | None:
    """تكييف مخرجات الموديل لواتساب."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t)                  # **bold** -> *bold*
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.MULTILINE)      # عناوين markdown
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.MULTILINE)   # قوائم markdown
    t = re.sub(r"\n{3,}", "\n\n", t)
    if len(t) > MAX_REPLY_CHARS:
        t = t[:MAX_REPLY_CHARS].rsplit("\n", 1)[0] + "\n…"
    return t or None


class Agent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, *, max_iterations: int = 5) -> None:
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations

    async def run(self, inp: AgentInput, ctx: ToolContext) -> AgentOutput:
        started = time.monotonic()
        out = AgentOutput(reply_text=None, status="ok", model=self.llm.model)
        system = build_system_prompt(inp.tenant, inp.customer, inp.now)
        messages: list[ChatMessage] = list(inp.history)
        specs = self.tools.specs()

        try:
            for _ in range(self.max_iterations):
                out.iterations += 1
                resp = await self.llm.complete(system=system, messages=messages, tools=specs)
                out.input_tokens += resp.usage.input_tokens
                out.output_tokens += resp.usage.output_tokens
                out.model = resp.model or out.model

                if not resp.tool_calls:
                    out.reply_text = clean_reply(resp.text)
                    if out.reply_text is None:
                        raise LLMError("empty reply")
                    break

                messages.append(ChatMessage("assistant", resp.text, tool_calls=resp.tool_calls))
                # أدوات مستقلة => بالتوازي؛ النتائج تُعاد بنفس ترتيب الاستدعاءات
                results = await asyncio.gather(*(self.tools.execute(ctx, c) for c in resp.tool_calls))
                for call, (payload, trace) in zip(resp.tool_calls, results):
                    out.traces.append(trace)
                    messages.append(ChatMessage("tool", payload, tool_call_id=call.id))
            else:
                raise LLMError(f"no final reply after {self.max_iterations} iterations")

        except LLMError as exc:
            log.error("agent: conversation %s failed: %s", inp.conversation_id, exc)
            out.status = "error" if out.iterations <= 1 else "fallback"
            out.error = str(exc)[:500]
            out.needs_human = True
            out.reply_text = FALLBACK_REPLY

        out.lead_id = ctx.lead_id
        out.handed_off = ctx.handed_off
        out.latency_ms = int((time.monotonic() - started) * 1000)
        return out
