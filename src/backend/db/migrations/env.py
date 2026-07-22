"""
Alembic async env.py

Key differences from the default sync env.py:
1. Uses AsyncEngine + run_sync() for the actual migration execution.
2. Imports ALL models via `backend.db.models` so autogenerate can detect schema changes.
3. Pulls the DB URL from our pydantic Settings object (SYNC_DATABASE_URL — asyncpg
   is not supported by Alembic's migration runner, so we use the psycopg2 URL).
4. compare_type=True: detects column type changes in autogenerate.
5. include_schemas=True: handles future multi-schema setups.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

# ── Ensure src/ is on the path so `backend.*` imports work ───────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection

# Import settings and the full model registry
from backend.core.config import get_settings
import backend.db.models  # noqa: F401 — registers all models with Base.metadata
from backend.db.base import Base

# ── Alembic Config ────────────────────────────────────────────────────────────
config = context.config
settings = get_settings()

# Override URL from our pydantic settings (sync driver for alembic)
config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Offline Migrations (no DB connection) ─────────────────────────────────────
def run_migrations_offline() -> None:
    """
    Emit SQL to stdout without a live DB connection.
    Useful for generating migration scripts for review.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online Migrations (with DB connection) ────────────────────────────────────
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,           # detect column type changes
        include_schemas=True,        # future-proof for multi-schema
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations using a synchronous psycopg2 connection.
    Alembic's migration runner does not support async natively.
    We use the SYNC_DATABASE_URL (postgresql://) here.
    """
    from sqlalchemy import create_engine

    connectable = create_engine(
        settings.SYNC_DATABASE_URL,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
