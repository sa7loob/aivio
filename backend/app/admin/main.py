"""Internal Admin API — separate process, never exposed publicly.

    uvicorn app.admin.main:app --host 127.0.0.1 --port 8001

الوصول: عبر SSH tunnel أو VPN فقط، + توكن مشرف (scripts/create_admin.py).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.admin.routers import billing, channels, tenants, vouchers
from app.billing.errors import to_http
from app.channels.meta_graph import MetaGraphClient
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.admin import dispose_admin_engine

log = logging.getLogger("admin")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.meta_http_timeout_seconds)) as http:
        app.state.graph = MetaGraphClient(http, base_url=settings.meta_graph_base_url,
                                          api_version=settings.meta_graph_api_version)
        yield
    await dispose_admin_engine()


app = FastAPI(title="Libya AI Commerce — Internal Admin", lifespan=lifespan)
for r in (tenants.router, tenants.users_router, channels.router, billing.router, vouchers.router):
    app.include_router(r)


@app.exception_handler(DBAPIError)
async def db_error_handler(_: Request, exc: DBAPIError) -> JSONResponse:
    """أخطاء المال من قاعدة البيانات (BL001 رصيد غير كافٍ ...) => أكواد HTTP واضحة."""
    http = to_http(exc)
    if http is not None:
        return JSONResponse(status_code=http.status_code, content=http.detail)
    log.exception("admin: unhandled database error")
    return JSONResponse(status_code=500, content={"error": "database_error"})


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
