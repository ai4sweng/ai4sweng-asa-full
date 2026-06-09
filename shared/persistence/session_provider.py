"""
SessionProvider — Unit of Work factory for PostgreSQL access.

SQLAlchemy AsyncSession is NOT safe for concurrent use across asyncio tasks.
Each concurrent operation opens its own AsyncSession, commits or rolls back
independently, then closes.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.persistence.database import get_session_factory
from shared.persistence.repositories import Repository


class SessionProvider:
    """
    Factory for isolated AsyncSession / Repository units of work.

    Use session_scope() for writes (auto-commit on success, rollback on error).
    Use read_scope() for read-only queries.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._factory = factory or get_session_factory()

    @asynccontextmanager
    async def session_scope(self) -> AsyncGenerator[Repository, None]:
        """Yield a Repository backed by a dedicated AsyncSession.
        Commits on clean exit; rolls back and re-raises on exception.
        Safe to call concurrently from independent asyncio tasks.
        """
        async with self._factory() as session:
            repo = Repository(session)
            try:
                yield repo
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def read_scope(self) -> AsyncGenerator[Repository, None]:
        """Yield a Repository for read-only queries (no commit)."""
        async with self._factory() as session:
            yield Repository(session)
