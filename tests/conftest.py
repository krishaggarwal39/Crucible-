"""
Test harness.

Two things here matter a lot:

1. TEST DATABASE ISOLATION.
   This suite truncates tables. It previously did so against
   `settings.DATABASE_URL`, which in a normal local setup is the *development*
   database — so running `pytest` silently destroyed local data. We now force
   POSTGRES_DB to a dedicated `<db>_test` database before any backend module is
   imported, and assert that the resolved name really is a test database before
   touching anything.

2. SCHEMA COMES FROM ALEMBIC, NOT create_all().
   Running the real migrations means the suite validates that migrations and
   models agree. That is what catches model/migration drift (e.g. an ondelete
   declared on a model but never migrated).
"""

import os
from pathlib import Path

# ── Force an isolated test database BEFORE importing anything from backend ────
# OS env vars take precedence over .env in pydantic-settings, so this wins.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# DATABASE_URL would override the POSTGRES_* parts, so make sure it is not set.
os.environ.pop("DATABASE_URL", None)

_base_db = os.environ.get("POSTGRES_DB", "crucible")
if not _base_db.endswith("_test"):
    os.environ["POSTGRES_DB"] = f"{_base_db}_test"

# Keep tests off the production code paths that depend on APP_ENV.
os.environ.setdefault("APP_ENV", "test")

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from sqlalchemy import pool, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import get_settings  # noqa: E402
from backend.db.base import Base  # noqa: E402
from backend.db.session import get_db  # noqa: E402
from backend.main import app  # noqa: E402
import backend.db.models  # noqa: F401,E402  — registers all tables on Base.metadata

settings = get_settings()

# ── Safety net ────────────────────────────────────────────────────────────────
if not settings.POSTGRES_DB.endswith("_test"):
    raise RuntimeError(
        "Refusing to run the test suite against a non-test database "
        f"({settings.POSTGRES_DB!r}). The suite truncates tables. "
        "Set POSTGRES_DB to a name ending in '_test'."
    )

SYNC_DSN = settings.SYNC_DATABASE_URL
ASYNCPG_DSN = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


def _maintenance_dsn() -> str:
    """DSN pointing at the 'postgres' maintenance DB, used to CREATE DATABASE."""
    return ASYNCPG_DSN.rsplit("/", 1)[0] + "/postgres"


async def _ensure_database_exists() -> None:
    conn = await asyncpg.connect(_maintenance_dsn())
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", settings.POSTGRES_DB
        )
        if not exists:
            # Identifier cannot be parameterised; the value is derived from our
            # own settings and validated to end with '_test' above.
            await conn.execute(f'CREATE DATABASE "{settings.POSTGRES_DB}"')
    finally:
        await conn.close()


def _run_migrations() -> None:
    """Bring the test database up to head using the real migration chain."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(PROJECT_ROOT / "src" / "backend" / "db" / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", SYNC_DSN)
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_database():
    """Create the test database (if needed) and migrate it to head, once."""
    await _ensure_database_exists()
    _run_migrations()
    yield


@pytest_asyncio.fixture
async def test_engine(_prepare_database):
    eng = create_async_engine(settings.DATABASE_URL, poolclass=pool.NullPool)
    yield eng
    await eng.dispose()


async def _truncate_all(engine) -> None:
    """
    Truncate every mapped table in one statement.

    Deriving the list from Base.metadata means new models are covered
    automatically, instead of relying on TRUNCATE ... CASCADE reaching them.
    """
    table_names = [f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables)]
    if not table_names:
        return
    stmt = f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE"
    async with engine.begin() as conn:
        await conn.execute(text(stmt))


@pytest_asyncio.fixture
async def test_session(test_engine):
    await _truncate_all(test_engine)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(test_session):
    async def override_get_db():
        # Mirror the real get_db(): commit on success, roll back on error.
        # The previous override never committed, so routes under test ran with
        # different transaction semantics than production.
        try:
            yield test_session
            await test_session.commit()
        except Exception:
            await test_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
