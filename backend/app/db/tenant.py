"""Tenant-scoped database sessions.

القاعدة الذهبية: أي وصول لبيانات وكالة يمر عبر tenant_session().
لا يوجد أي مسار آخر في الكود يضبط app.tenant_id.

لماذا set_config(..., true) وليس SET LOCAL؟
    SET LOCAL لا يقبل bind parameters، فكنا سنضطر لدمج القيمة في نص SQL (خطر SQL injection).
    set_config('app.tenant_id', :tid, true) مطابقة تماماً لـ SET LOCAL: القيمة تنتهي مع نهاية
    الـ transaction، فلا "تتسرب" إلى طلب آخر يعيد استخدام نفس الاتصال من الـ pool
    (وتعمل كذلك مع PgBouncer في وضع transaction pooling).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()
# يتصل بدور app_user (NOBYPASSRLS) — لا يُستخدم app_owner في التشغيل أبداً
engine = create_async_engine(
    _settings.database_url, pool_size=_settings.db_pool_size, pool_pre_ping=True
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def dispose_engine() -> None:
    await engine.dispose()


@asynccontextmanager
async def tenant_session(tenant_id: UUID, user_id: UUID | None = None) -> AsyncIterator[AsyncSession]:
    """Transaction مقيّدة بوكالة واحدة. commit عند النجاح و rollback عند أي استثناء.
    user_id (اختياري): المستخدم الذي يعمل الآن (طلبات لوحة التحكم)، للدوال والسياسات التي تحتاجه."""
    if not isinstance(tenant_id, UUID):
        raise TypeError("tenant_id must be a UUID")
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true), "
                     "set_config('app.user_id', coalesce(:uid, ''), true)"),
                {"tid": str(tenant_id), "uid": str(user_id) if user_id else None},
            )
            yield session


@asynccontextmanager
async def user_session(user_id: UUID) -> AsyncIterator[AsyncSession]:
    """سياق مستخدم بدون وكالة (قبل اختيار الوكالة): my_tenants(), register_tenant(), accept_invitation()."""
    if not isinstance(user_id, UUID):
        raise TypeError("user_id must be a UUID")
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text("SELECT set_config('app.user_id', :uid, true)"),
                                  {"uid": str(user_id)})
            yield session


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncSession]:
    """بدون وكالة: للـ webhook ingest ودوال الـ worker (resolve/claim) فقط.
    أي استعلام على جدول محمي هنا يعيد صفر صفوف (Fail-closed)."""
    async with SessionLocal() as session:
        async with session.begin():
            yield session
