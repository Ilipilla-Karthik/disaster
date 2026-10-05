"""Async database session and engine setup.

Supports PostgreSQL (asyncpg) for deployment and SQLite (aiosqlite) for
zero-dependency local demonstration. The public surface is intentionally small:
``engine``, ``AsyncSessionLocal``, ``Base``, ``get_db`` and ``init_db``.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)


def _async_url() -> str:
    """Return the effective async SQLAlchemy URL."""
    url = settings.DATABASE_URL
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


ASYNC_URL = _async_url()
USING_SQLITE = ASYNC_URL.startswith("sqlite")

engine: AsyncEngine = create_async_engine(
    ASYNC_URL,
    echo=settings.DB_ECHO,
    future=True,
    # SQLite ignores pool sizing; Postgres benefits from it.
    **({} if USING_SQLITE else {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True}),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create tables for all registered models.

    A development/demo convenience, enabled by AUTO_CREATE_TABLES. It infers the
    schema from the model definitions, so it will not evolve an existing
    database safely -- a production deployment that changes a model needs a
    real migration tool (Alembic) before it changes the schema, otherwise the
    existing tables are simply left as they are.
    """
    from app.models import models  # noqa: F401  (registers tables on Base.metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema ensured at %s", ASYNC_URL)


async def verify_connection() -> bool:
    """Return True if the configured database accepts connections."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Database connectivity check failed: %s", exc)
        return False


def use_sqlite_fallback() -> None:
    """Rebind the engine/sessionmaker to SQLite.

    Used at startup when PostgreSQL is unreachable so a fresh clone can still be
    demonstrated without provisioning a database. Never used when
    ``DB_FALLBACK_TO_SQLITE`` is disabled.
    """
    global engine, AsyncSessionLocal, ASYNC_URL, USING_SQLITE

    if USING_SQLITE:
        return

    logger.warning("Falling back to SQLite at %s", settings.SQLITE_URL)
    ASYNC_URL = settings.SQLITE_URL
    USING_SQLITE = True
    engine = create_async_engine(ASYNC_URL, echo=settings.DB_ECHO, future=True)
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )