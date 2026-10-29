"""SQLAlchemy database setup.

Provides BOTH:
- Async engine + ``get_db`` dependency for FastAPI request handlers.
- Sync engine + ``SyncSessionLocal`` for Celery workers (where running async
  code is awkward and the workload is mostly synchronous OCR/embedding/IO).
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


def _engine_kwargs_for_url(url: str, *, sync: bool = False) -> dict[str, object]:
    """Return backend-appropriate pooling options.

    SQLite engines do not accept PostgreSQL-specific connection-pool arguments
    such as ``pool_size`` and ``max_overflow``. The test suite and local memory
    database use ``sqlite+aiosqlite:///:memory:``, so we keep those options only
    for non-SQLite backends.
    """

    kwargs: dict[str, object] = {"echo": False, "pool_pre_ping": True, "pool_recycle": 3600}
    if not url.startswith("sqlite"):
        kwargs.update({"pool_size": 10 if not sync else 5, "max_overflow": 20 if not sync else 10})
    return kwargs


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- async ----
engine = create_async_engine(
    settings.database_url,
    **_engine_kwargs_for_url(settings.database_url),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Verify database connectivity; schema changes are managed by Alembic."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connection initialized")


async def close_db() -> None:
    await engine.dispose()


# ---------------------------------------------------------------- sync -----
sync_engine = create_engine(
    settings.sync_database_url,
    **_engine_kwargs_for_url(settings.sync_database_url, sync=True),
    future=True,
)

SyncSessionLocal: sessionmaker[Session] = sessionmaker(
    bind=sync_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)
