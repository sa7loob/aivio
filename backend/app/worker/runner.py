"""Worker process:  python -m app.worker.runner

حلقات مستقلة تعمل بالتوازي في نفس العملية:
    ingest  : webhook_events         -> messages (+ debounce، ومهمة تفريغ للرسائل الصوتية)
    reply   : conversations المستحقة -> outbound_messages
    send    : outbound_messages      -> WhatsApp Cloud API
    voice   : ai_jobs (transcribe)   -> نص الرسالة الصوتية (حساسة لزمن الرد، منفصلة)
    ai      : ai_jobs (catalog_extract, embed_knowledge) -> مسودات الكتالوج / embeddings

يمكن تشغيل أكثر من نسخة من الـ worker بأمان (FOR UPDATE SKIP LOCKED + leases).
"""
from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

import httpx

from app.channels.registry import SenderRegistry
from app.channels.messenger import MessengerClient
from app.channels.meta_graph import MetaGraphClient
from app.worker.health import run_channel_health_tick
from app.channels.whatsapp import WhatsAppClient
from app.agent.core import Agent
from app.agent.tools import default_registry
from app.core.config import Settings, get_settings
from app.llm.openai_client import (
    OpenAIChatClient,
    OpenAIDocumentExtractor,
    OpenAIEmbeddingClient,
    OpenAITranscriptionClient,
    create_openai_sdk_client,
)
from app.ai.catalog import CatalogExtractHandler
from app.ai.jobs import BACKGROUND_KINDS, VOICE_KINDS, AIDeps, JobHandler, run_ai_batch
from app.ai.knowledge import EmbedKnowledgeHandler
from app.ai.transcription import TranscribeHandler, media_tmp_dir, sweep_tmp_dir
from app.core.logging import setup_logging
from app.db.tenant import dispose_engine
from app.worker.ingest import run_ingest_batch
from app.worker.reply import ReplyDeps, run_reply_batch
from app.worker.sender import run_send_batch
from app.db import queries as q
from app.db.tenant import system_session

log = logging.getLogger("worker")


async def _loop(name: str, step: Callable[[], Awaitable[int]], settings: Settings,
                stop: asyncio.Event) -> None:
    error_streak = 0
    while not stop.is_set():
        try:
            processed = await step()
            error_streak = 0
        except Exception:  # noqa: BLE001 — مثلاً قاعدة البيانات غير متاحة مؤقتاً
            error_streak += 1
            log.exception("%s loop error (streak=%d)", name, error_streak)
            processed = 0
        if processed == 0:
            # لا عمل: انتظر (أطول عند تكرار الأخطاء) أو اخرج فوراً عند الإيقاف
            delay = settings.worker_poll_interval_seconds * (2 ** min(error_streak, 6))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
    log.info("%s loop stopped", name)


def create_sdk(settings: Settings):
    if settings.openai_api_key is None:
        raise SystemExit("OPENAI_API_KEY is required for the worker")
    return create_openai_sdk_client(settings.openai_api_key.get_secret_value(),
                                    timeout=settings.llm_timeout_seconds,
                                    max_retries=settings.llm_max_retries)


def build_reply_deps(settings: Settings, sdk) -> ReplyDeps:
    llm = OpenAIChatClient(sdk, model=settings.llm_model, temperature=settings.llm_temperature,
                           max_output_tokens=settings.llm_max_output_tokens)
    embedder = OpenAIEmbeddingClient(sdk, model=settings.embedding_model,
                                     dimensions=settings.embedding_dimensions)
    agent = Agent(llm, default_registry(), max_iterations=settings.agent_max_tool_iterations)
    return ReplyDeps(settings=settings, agent=agent, embedder=embedder)


def build_ai_deps(settings: Settings, sdk, graph: MetaGraphClient) -> AIDeps:
    return AIDeps(
        settings=settings, graph=graph,
        transcriber=OpenAITranscriptionClient(sdk, model=settings.transcription_model),
        extractor=OpenAIDocumentExtractor(sdk, model=settings.catalog_extraction_model,
                                          max_output_tokens=settings.catalog_extraction_max_output_tokens),
        embedder=OpenAIEmbeddingClient(sdk, model=settings.embedding_model,
                                       dimensions=settings.embedding_dimensions),
    )


VOICE_HANDLERS: dict[str, JobHandler] = {"transcribe": TranscribeHandler()}
BACKGROUND_HANDLERS: dict[str, JobHandler] = {"catalog_extract": CatalogExtractHandler(),
                                              "embed_knowledge": EmbedKnowledgeHandler()}
assert set(VOICE_HANDLERS) == set(VOICE_KINDS) and set(BACKGROUND_HANDLERS) == set(BACKGROUND_KINDS)


BILLING_TICK_SECONDS = 3600
HEALTH_TICK_SECONDS = 600       # كل 10 دقائق نحجز القنوات المستحقة (كل قناة تُفحص كل N ساعات)


async def run_billing_tick() -> None:
    """تجديد تلقائي من المحافظ / مهلة / إيقاف. الدالة نفسها آمنة للتشغيل المتوازي (SKIP LOCKED)."""
    async with system_session() as s:
        changes = (await s.execute(q.SUBSCRIPTION_LIFECYCLE_TICK)).mappings().all()
    for c in changes:
        log.info("billing: tenant %s %s -> %s", c["tenant_id"], c["old_status"], c["new_status"])


async def _periodic(name: str, fn: Callable[[], Awaitable[None]], every: float,
                    stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await fn()
        except Exception:  # noqa: BLE001
            log.exception("%s periodic task failed", name)
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    sdk = create_sdk(settings)                # فشل مبكر إذا نقص مفتاح الـ API
    reply_deps = build_reply_deps(settings, sdk)
    swept = sweep_tmp_dir(media_tmp_dir(settings))
    if swept:
        log.warning("voice: removed %d stale temp audio file(s)", swept)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)  # docker stop => إنهاء الدفعة الحالية ثم الخروج

    timeout = httpx.Timeout(settings.meta_http_timeout_seconds, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        senders = SenderRegistry({
            "whatsapp": WhatsAppClient(http, base_url=settings.meta_graph_base_url,
                                       api_version=settings.meta_graph_api_version),
            "messenger": MessengerClient(http, base_url=settings.meta_graph_base_url,
                                         api_version=settings.meta_graph_api_version, channel="messenger"),
            "instagram": MessengerClient(http, base_url=settings.meta_graph_base_url,
                                         api_version=settings.meta_graph_api_version, channel="instagram"),
        })
        graph = MetaGraphClient(http, base_url=settings.meta_graph_base_url,
                                api_version=settings.meta_graph_api_version)

        ai_deps = build_ai_deps(settings, sdk, graph)

        async def health_tick() -> None:
            await run_channel_health_tick(graph, settings)

        log.info("worker started")
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_loop("ingest", lambda: run_ingest_batch(settings), settings, stop))
                tg.create_task(_loop("reply", lambda: run_reply_batch(reply_deps), settings, stop))
                tg.create_task(_loop("send", lambda: run_send_batch(settings, senders), settings, stop))
                tg.create_task(_loop("voice", lambda: run_ai_batch(ai_deps, VOICE_HANDLERS), settings, stop))
                tg.create_task(_loop("ai", lambda: run_ai_batch(ai_deps, BACKGROUND_HANDLERS), settings, stop))
                tg.create_task(_periodic("billing", run_billing_tick, BILLING_TICK_SECONDS, stop))
                tg.create_task(_periodic("channel-health", health_tick, HEALTH_TICK_SECONDS, stop))
        finally:
            await dispose_engine()
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
