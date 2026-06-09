"""Tests for shared.persistence.session_provider — scoping and context management."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.persistence.repositories import Repository
from shared.persistence.session_provider import SessionProvider


def _make_factory() -> MagicMock:
    """Return a mock async_sessionmaker that yields an AsyncSession context manager."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    @asynccontextmanager
    async def _session_cm():
        yield session

    factory = MagicMock(return_value=_session_cm())
    return factory, session


class TestSessionProvider:
    def test_instantiation_with_factory(self):
        factory, _ = _make_factory()
        sp = SessionProvider(factory=factory)
        assert sp is not None

    @pytest.mark.asyncio
    async def test_session_scope_yields_repository(self):
        factory, session = _make_factory()
        sp = SessionProvider(factory=factory)
        async with sp.session_scope() as repo:
            assert isinstance(repo, Repository)

    @pytest.mark.asyncio
    async def test_session_scope_commits_on_success(self):
        factory, session = _make_factory()
        sp = SessionProvider(factory=factory)
        async with sp.session_scope():
            pass
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_scope_rolls_back_on_error(self):
        factory, session = _make_factory()
        sp = SessionProvider(factory=factory)
        with pytest.raises(ValueError, match="boom"):
            async with sp.session_scope():
                raise ValueError("boom")
        session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_read_scope_yields_repository(self):
        factory, session = _make_factory()
        sp = SessionProvider(factory=factory)
        async with sp.read_scope() as repo:
            assert isinstance(repo, Repository)

    @pytest.mark.asyncio
    async def test_read_scope_does_not_commit(self):
        factory, session = _make_factory()
        sp = SessionProvider(factory=factory)
        async with sp.read_scope():
            pass
        session.commit.assert_not_awaited()
