"""
Async database engine and session factory.

Design decisions:
- Single engine per process, created at startup.
- AsyncSession with expire_on_commit=False avoids lazy-load errors after commit.
- get_db() is a FastAPI dependency that yields a session and handles commit/rollback.
- pool_pre_ping=True catches stale connections (e.g. after Docker restart).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import get_settings

settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────
# pool_size / max_overflow are tuned for a single-process dev setup.
# For production, adjust based on expected concurrency.
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,           # SQL logging in dev only
    pool_pre_ping=True,            # detect stale connections
    pool_size=10,
    max_overflow=20,
    connect_args={"timeout": 10},
)

# ── Session Factory ───────────────────────────────────────────────────────────
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,        # avoid DetachedInstanceError after commit
    autoflush=False,               # explicit flush gives us more control
)


# ── FastAPI Dependency ────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session for the duration of a request.

    Commits if no exception, rolls back otherwise.
    The `async with` context manager handles session.close() automatically.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
