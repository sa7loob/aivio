"""Database access for the internal Admin API (role app_admin, BYPASSRLS).

يُستخدم فقط من app/admin — لا يُستورد أبداً في الـ API العام أو الـ worker.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache
def _engine() -> AsyncEngine:
    url = get_settings().admin_database_url
    if not url:
        raise RuntimeError("ADMIN_DATABASE_URL is not set (role app_admin)")
    return create_async_engine(url, pool_size=5, pool_pre_ping=True)


@lru_cache
def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_engine(), expire_on_commit=False)


@asynccontextmanager
async def admin_session(tenant_id: UUID | None = None) -> AsyncIterator[AsyncSession]:
    """Transaction واحدة. tenant_id اختياري: يضبط app.tenant_id للدوال التي تعتمد عليه
    (redeem_voucher) ولتكون القيم الافتراضية مثل app_current_tenant_id() صحيحة."""
    async with _sessionmaker()() as session:
        async with session.begin():
            if tenant_id is not None:
                await session.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                                      {"t": str(tenant_id)})
            yield session


async def dispose_admin_engine() -> None:
    if _engine.cache_info().currsize:
        await _engine().dispose()
