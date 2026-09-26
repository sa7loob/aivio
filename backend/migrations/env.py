"""Alembic environment.

القرار: الـ migrations مكتوبة بـ SQL صريح (op.execute) وليس autogenerate،
لأن RLS والسياسات والأعمدة المولّدة والدوال لا يلتقطها autogenerate بشكل موثوق.
نماذج SQLAlchemy (المرحلة 3) تعكس هذا الـ schema ولا تولّده.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _url() -> str:
    url = os.environ.get("MIGRATIONS_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATIONS_DATABASE_URL is not set (must use the app_owner role)")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
