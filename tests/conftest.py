import pytest
import pytest_asyncio
import httpx
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import pool

from backend.core.config import get_settings
from backend.main import app
from backend.db.session import get_db

settings = get_settings()

@pytest_asyncio.fixture
async def test_engine():
    eng = create_async_engine(settings.DATABASE_URL, poolclass=pool.NullPool)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine):
    # Truncate tables using raw asyncpg to avoid duplication across tests
    import asyncpg
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE TABLE users, tenants CASCADE")
    finally:
        await conn.close()
        
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(test_session):
    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
