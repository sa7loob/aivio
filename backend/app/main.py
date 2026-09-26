"""FastAPI entrypoint:  uvicorn app.main:app --host 0.0.0.0 --port 8000"""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.meta_callbacks import router as meta_callbacks_router
from app.api.v1.auth import router as auth_router
from app.api.v1.events import router as events_router
from app.api.v1.inbox import router as inbox_router
from app.api.v1.leads import router as leads_router
from app.api.v1.onboarding import router as onboarding_router
from app.api.webhooks import router as webhooks_router
from app.channels.meta_graph import MetaGraphClient
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.tenant import SessionLocal, dispose_engine
from app.realtime.hub import EventHub, asyncpg_connector

CONNECT_PAGE = pathlib.Path(__file__).parent / "web" / "connect.html"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.meta_http_timeout_seconds)) as http:
        app.state.graph = MetaGraphClient(http, base_url=settings.meta_graph_base_url,
                                          api_version=settings.meta_graph_api_version)
        app.state.hub = EventHub(asyncpg_connector(settings.database_url))
        await app.state.hub.start()
        try:
            yield
        finally:
            await app.state.hub.stop()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Libya AI Commerce Agent",
        lifespan=lifespan,
        docs_url="/docs" if settings.env == "dev" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.env == "dev" else None,
    )
    app.include_router(webhooks_router)
    app.include_router(auth_router)
    app.include_router(onboarding_router)
    app.include_router(meta_callbacks_router)
    app.include_router(inbox_router)
    app.include_router(leads_router)
    app.include_router(events_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/connect", include_in_schema=False, response_class=HTMLResponse)
    async def connect_page() -> HTMLResponse:
        """صفحة ربط القنوات المؤقتة حتى لوحة التحكم (المرحلة 6). تستخدم نفس الـ API."""
        return HTMLResponse(CONNECT_PAGE.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    return app


app = create_app()
