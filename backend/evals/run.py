"""Run the agent against evals/cases.yaml using the REAL LLM, tools and database.

    python -m evals.run                 # كل الحالات
    python -m evals.run --case booking_flow --case price_mawlid
    python -m evals.run --judge         # + تقييم أسلوب/دقة بواسطة LLM (تكلفة إضافية)

المتطلبات: قاعدة بيانات بعد alembic upgrade، OPENAI_API_KEY، MIGRATIONS_DATABASE_URL (للتهيئة)،
و DATABASE_URL (دور app_user الذي تعمل به الأدوات فعلياً مع RLS).
لا يُرسل أي شيء لواتساب: الـ Agent يُستدعى مباشرة بدون حلقة الإرسال.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import create_engine, text

from app.agent.core import Agent
from app.agent.knowledge_ingest import chunk_entries, embed_chunks, insert_chunks
from app.agent.tools import ToolRegistry, default_registry
from app.agent.types import AgentInput, CustomerProfile, TenantProfile, ToolContext, ToolTrace
from app.core.config import get_settings
from app.db.tenant import dispose_engine, tenant_session
from app.llm.base import ChatMessage, LLMClient, ToolCall
from app.llm.openai_client import OpenAIChatClient, OpenAIEmbeddingClient, create_openai_sdk_client
from evals.checks import CaseResult, check_case

HERE = pathlib.Path(__file__).parent
TENANT_ID = UUID("eeeeeeee-0000-0000-0000-000000000001")
ACCOUNT_ID = UUID("eeeeeeee-0000-0000-0000-0000000000c1")
CUSTOMER_WA = "218913334444"


class RecordingRegistry(ToolRegistry):
    """يسجّل نتائج الأدوات أيضاً (للـ judge ولتشخيص الأخطاء)."""

    def __init__(self, inner: ToolRegistry) -> None:
        super().__init__(list(inner._tools.values()))
        self.log: list[dict[str, Any]] = []

    async def execute(self, ctx: ToolContext, call: ToolCall) -> tuple[str, ToolTrace]:
        payload, trace = await super().execute(ctx, call)
        self.log.append({"name": trace.name, "args": trace.args, "ok": trace.ok,
                         "result": payload[:1500]})
        return payload, trace


# ----------------------------------------------------------------------------- setup
def setup_database(owner_url: str) -> Any:
    engine = create_engine(owner_url)
    # ملف SQL متعدد الجمل: نمرره للـ driver مباشرة بدون parameters (لا تفسير لعلامة %)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute((HERE / "fixtures" / "eval_seed.sql").read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()
    return engine


async def load_knowledge(engine: Any, embedder: OpenAIEmbeddingClient, model: str) -> int:
    entries = yaml.safe_load((HERE / "fixtures" / "knowledge.yaml").read_text(encoding="utf-8"))
    chunks = chunk_entries(entries)
    await embed_chunks(chunks, embedder)
    with engine.begin() as conn:
        return insert_chunks(conn, TENANT_ID, chunks, source_ref="eval-knowledge.yaml",
                             embedding_model=model)


def new_conversation(engine: Any, case_id: str) -> tuple[UUID, UUID]:
    """زبون + محادثة جديدة لكل حالة (نفس رقم الواتساب، معرّف خارجي مختلف)."""
    with engine.begin() as conn:
        contact_id = conn.execute(text("""
            INSERT INTO contacts (tenant_id, channel, external_user_id, display_name, phone_e164)
            VALUES (:t, 'whatsapp', :ext, 'Ali', :phone) RETURNING id
        """), {"t": TENANT_ID, "ext": f"{CUSTOMER_WA}-{case_id}-{time.time_ns()}",
               "phone": f"+{CUSTOMER_WA}"}).scalar_one()
        conv_id = conn.execute(text("""
            INSERT INTO conversations (tenant_id, contact_id, channel_account_id, last_inbound_at)
            VALUES (:t, :c, :a, now()) RETURNING id
        """), {"t": TENANT_ID, "c": contact_id, "a": ACCOUNT_ID}).scalar_one()
    return contact_id, conv_id


def lead_state(engine: Any, contact_id: UUID) -> tuple[dict[str, Any] | None, int]:
    with engine.begin() as conn:
        lead = conn.execute(text("""
            SELECT l.id, l.adults, l.children, l.infants, l.room_type_pref, l.phone_e164,
                   l.full_name, p.code AS package_code
              FROM leads l LEFT JOIN packages p ON p.id = l.package_id
             WHERE l.contact_id = :c ORDER BY l.created_at DESC LIMIT 1
        """), {"c": contact_id}).mappings().first()
        if lead is None:
            return None, 0
        notified = conn.execute(text("""
            SELECT count(*) FROM outbound_messages
             WHERE lead_id = :l AND purpose = 'staff_notification'
        """), {"l": lead["id"]}).scalar_one()
    return dict(lead), notified


# ----------------------------------------------------------------------------- judge
JUDGE_PROMPT = """You are grading a customer-service assistant for a Libyan Hajj/Umrah travel agency.
Given the conversation and the tool results the assistant saw, return ONLY a JSON object:
{"dialect": 1-5, "helpfulness": 1-5, "grounded": true|false, "notes": "<short>"}
- dialect: 5 = natural, polite Libyan Arabic (or the customer's language if not Arabic); 1 = stiff MSA or another dialect.
- helpfulness: answered the actual question and moved the customer forward.
- grounded: every price/date/policy stated appears in the tool results; false if anything was invented."""


async def judge(llm: LLMClient, transcript: list[ChatMessage], tool_log: list[dict[str, Any]]) -> dict[str, Any]:
    convo = "\n".join(f"{m.role.upper()}: {m.content}" for m in transcript)
    tools = json.dumps(tool_log, ensure_ascii=False)[:6000]
    resp = await llm.complete(system=JUDGE_PROMPT, tools=[], messages=[
        ChatMessage("user", f"CONVERSATION:\n{convo}\n\nTOOL RESULTS:\n{tools}")])
    raw = (resp.text or "").strip().removeprefix("```json").removesuffix("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "unparseable judge output", "raw": raw[:200]}


# ----------------------------------------------------------------------------- run
async def run_case(case: dict[str, Any], engine: Any, agent: Agent, embedder: Any,
                   judge_llm: LLMClient | None) -> CaseResult:
    settings = get_settings()
    contact_id, conv_id = new_conversation(engine, case["id"])
    customer = CustomerProfile(contact_id=contact_id, channel="whatsapp", display_name="Ali",
                               phone_e164=f"+{CUSTOMER_WA}")
    with engine.begin() as conn:
        t = conn.execute(text("SELECT name, settings FROM tenants WHERE id = :t"),
                         {"t": TENANT_ID}).mappings().one()
    tenant = TenantProfile(tenant_id=TENANT_ID, name=t["name"], settings=t["settings"] or {})

    registry = agent.tools
    assert isinstance(registry, RecordingRegistry)
    registry.log.clear()
    ctx = ToolContext(tenant_id=TENANT_ID, conversation_id=conv_id, customer=customer,
                      session_factory=tenant_session, embedder=embedder,
                      price_stale_days=settings.agent_price_stale_days)

    history: list[ChatMessage] = []
    result = CaseResult(id=case["id"], passed=False)
    calls: list[tuple[str, dict[str, Any] | None, bool]] = []
    for turn in case["turns"]:
        history.append(ChatMessage("user", turn))
        out = await agent.run(AgentInput(tenant=tenant, customer=customer, conversation_id=conv_id,
                                         history=list(history), now=datetime.now(timezone.utc)), ctx)
        reply = out.reply_text or ""
        history.append(ChatMessage("assistant", reply))
        result.replies.append(reply)
        result.tokens += out.input_tokens + out.output_tokens
        result.latency_ms += out.latency_ms
        calls.extend((tr.name, tr.args, tr.ok) for tr in out.traces)
        if out.status != "ok":
            result.failures.append(f"agent status {out.status}: {out.error}")

    lead, notified = lead_state(engine, contact_id)
    failures, warnings = check_case(case.get("expect", {}), calls, result.replies[-1], lead, notified)
    result.failures += failures
    result.warnings = warnings
    result.tools = [c[0] for c in calls]
    if judge_llm is not None:
        result.judge = await judge(judge_llm, history, registry.log)
        j = result.judge
        if j.get("grounded") is False:
            result.failures.append(f"judge: not grounded ({j.get('notes')})")
        if isinstance(j.get("dialect"), int) and j["dialect"] < 3:
            result.warnings.append(f"judge: weak dialect ({j['dialect']}/5)")
    result.passed = not result.failures
    return result


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    owner_url = os.environ.get("MIGRATIONS_DATABASE_URL")
    if not owner_url or settings.openai_api_key is None:
        print("MIGRATIONS_DATABASE_URL and OPENAI_API_KEY are required", file=sys.stderr)
        return 2

    cases = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    if args.case:
        cases = [c for c in cases if c["id"] in set(args.case)]

    sdk = create_openai_sdk_client(settings.openai_api_key.get_secret_value(),
                                   timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries)
    llm = OpenAIChatClient(sdk, model=args.model or settings.llm_model,
                           temperature=settings.llm_temperature,
                           max_output_tokens=settings.llm_max_output_tokens)
    embedder = OpenAIEmbeddingClient(sdk, model=settings.embedding_model,
                                     dimensions=settings.embedding_dimensions)
    agent = Agent(llm, RecordingRegistry(default_registry()),
                  max_iterations=settings.agent_max_tool_iterations)

    engine = setup_database(owner_url)
    n = await load_knowledge(engine, embedder, settings.embedding_model)
    print(f"seeded eval tenant, {n} knowledge chunks. model={llm.model}\n")

    results: list[CaseResult] = []
    for case in cases:
        r = await run_case(case, engine, agent, embedder, llm if args.judge else None)
        results.append(r)
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.id:<22} tools={r.tools}")
        print(f"       ↳ {r.replies[-1][:160]!r}")
        for f in r.failures:
            print(f"       ✗ {f}")
        for w in r.warnings:
            print(f"       ! {w}")
        if r.judge:
            print(f"       judge: {r.judge}")

    passed = sum(r.passed for r in results)
    tokens = sum(r.tokens for r in results)
    print(f"\n{passed}/{len(results)} passed · {tokens} tokens")

    report = HERE / "reports" / f"{datetime.now():%Y%m%d-%H%M%S}-{llm.model}.json"
    report.write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1,
                                 default=str), encoding="utf-8")
    print(f"report: {report}")
    await dispose_engine()
    return 0 if passed == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", help="شغّل حالة محددة (يمكن التكرار)")
    ap.add_argument("--judge", action="store_true", help="تقييم إضافي بواسطة LLM")
    ap.add_argument("--model", help="جرّب موديل آخر بدون تعديل .env")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
