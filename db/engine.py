"""Async SQLAlchemy engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

_is_pg = "postgresql" in settings.database_url

_engine_kwargs: dict = {"echo": False}
if _is_pg:
    # PostgreSQL: configurable pool tuned for higher concurrent workload.
    _engine_kwargs.update(
        pool_size=max(1, settings.db_pool_size),
        max_overflow=max(0, settings.db_max_overflow),
        pool_timeout=max(1, settings.db_pool_timeout_seconds),
        pool_recycle=max(60, settings.db_pool_recycle_seconds),
        pool_pre_ping=True,
    )
else:
    # SQLite: use NullPool to avoid thread/coroutine contention issues.
    from sqlalchemy.pool import NullPool

    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.database_url, **_engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Create all tables (dev convenience; use Alembic for production)."""
    from db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
