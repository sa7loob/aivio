"""ai_jobs worker: claim -> handler (بدون transaction أثناء استدعاء Meta/OpenAI) -> done / retry / failed.

نفس نمط sender.py:
    claim (system_session، دالة SECURITY DEFINER تعيد معرّفات فقط)
    لكل مهمة: tx قصيرة للقراءة داخل سياق الوكالة -> الاستدعاء الخارجي -> tx قصيرة للكتابة
الـ lease يمنع worker آخر من أخذ نفس المهمة؛ تعطل العملية => ينتهي الـ lease وتُعاد المحاولة.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.ai import queries as aq
from app.channels.meta_graph import MetaGraphClient
from app.core.config import Settings
from app.db.tenant import system_session, tenant_session
from app.llm.base import DocumentExtractor, EmbeddingClient, TranscriptionClient
from app.worker.retry import backoff_seconds

log = logging.getLogger(__name__)

VOICE_KINDS = ("transcribe",)
BACKGROUND_KINDS = ("catalog_extract", "embed_knowledge")


@dataclass(frozen=True)
class AIDeps:
    settings: Settings
    graph: MetaGraphClient | None
    transcriber: TranscriptionClient | None
    extractor: DocumentExtractor | None
    embedder: EmbeddingClient | None


@dataclass(frozen=True)
class Job:
    id: UUID
    tenant_id: UUID
    kind: str
    attempts: int
    message_id: UUID | None = None
    catalog_import_id: UUID | None = None
    knowledge_chunk_id: UUID | None = None


@dataclass
class JobResult:
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    note: str | None = None          # يُحفظ في last_error للمهام المنتهية بملاحظة (مثلاً: تفريغ فارغ)


class PermanentJobError(Exception):
    """لا فائدة من إعادة المحاولة: ملف كبير، صيغة غير مدعومة، وسائط منتهية، رفض الموديل..."""


class JobHandler(Protocol):
    kind: str
    max_attempts: int
    retry_base_seconds: float

    async def run(self, deps: AIDeps, job: Job) -> JobResult: ...

    async def on_final_failure(self, deps: AIDeps, job: Job, error: str) -> None: ...


async def run_ai_batch(deps: AIDeps, handlers: Mapping[str, JobHandler]) -> int:
    s_ = deps.settings
    async with system_session() as s:
        claimed = (await s.execute(aq.CLAIM_AI_JOBS, {
            "kinds": list(handlers), "batch": s_.worker_ai_batch,
            "lease_seconds": float(s_.worker_ai_lease_seconds),
        })).mappings().all()
    # مهام مختلفة (غالباً وكالات مختلفة) => بالتوازي
    await asyncio.gather(*(run_one(deps, handlers, row["tenant_id"], row["job_id"]) for row in claimed))
    return len(claimed)


async def run_one(deps: AIDeps, handlers: Mapping[str, JobHandler], tenant_id: UUID, job_id: UUID) -> None:
    try:
        async with tenant_session(tenant_id) as s:
            row = (await s.execute(aq.LOAD_AI_JOB, {"job_id": job_id})).mappings().first()
        if row is None:
            return                                        # انتهت من worker آخر
        job = Job(id=row["id"], tenant_id=tenant_id, kind=row["kind"], attempts=row["attempts"],
                  message_id=row["message_id"], catalog_import_id=row["catalog_import_id"],
                  knowledge_chunk_id=row["knowledge_chunk_id"])
        await _execute(deps, handlers[job.kind], job)
    except Exception:  # noqa: BLE001 — الـ lease ينتهي وتُعاد المحاولة
        log.exception("ai: job %s crashed", job_id)


async def _execute(deps: AIDeps, handler: JobHandler, job: Job) -> None:
    started = time.monotonic()
    try:
        result = await handler.run(deps, job)
    except PermanentJobError as exc:
        await _fail(deps, handler, job, f"permanent: {exc}"[:1000], started)
        return
    except Exception as exc:  # noqa: BLE001 — شبكة / 5xx / أخطاء الموديل => إعادة محاولة
        error = f"{type(exc).__name__}: {exc}"[:1000]
        if job.attempts >= handler.max_attempts:
            await _fail(deps, handler, job, error, started)
            return
        delay = backoff_seconds(job.attempts, base=handler.retry_base_seconds, cap=600.0)
        async with tenant_session(job.tenant_id) as s:
            await s.execute(aq.RETRY_AI_JOB, {"job_id": job.id, "error": error, "delay_seconds": delay})
        log.warning("ai: %s job %s retry #%s in %.0fs: %s", job.kind, job.id, job.attempts, delay, error)
        return

    async with tenant_session(job.tenant_id) as s:
        await s.execute(aq.FINISH_AI_JOB, _finish_params(job, "done", result.note, result, started))
    log.info("ai: %s job %s done in %dms%s", job.kind, job.id, _elapsed_ms(started),
             f" ({result.note})" if result.note else "")


async def _fail(deps: AIDeps, handler: JobHandler, job: Job, error: str, started: float) -> None:
    await handler.on_final_failure(deps, job, error)
    async with tenant_session(job.tenant_id) as s:
        await s.execute(aq.FINISH_AI_JOB, _finish_params(job, "failed", error, JobResult(), started))
    log.error("ai: %s job %s failed: %s", job.kind, job.id, error)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _finish_params(job: Job, status: str, error: str | None, result: JobResult, started: float) -> dict[str, Any]:
    return {"job_id": job.id, "status": status, "error": error, "model": result.model,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "duration_ms": _elapsed_ms(started)}
