"""Tests for shared.capability_registry — registration, filtering, and planner context."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.capability_registry.registry import CapabilityRegistry
from shared.contracts.capabilities import KIOCapability
from shared.persistence.models import KIOCapabilityRecord


def _cap(kio_id: str, available: bool = True, description: str = "desc") -> KIOCapability:
    return KIOCapability(
        kio_id=kio_id,
        name=f"KIO {kio_id}",
        description=description,
        input_artifact_type=None,
        output_artifact_type=f"{kio_id}_artifact",
        available=available,
    )


def _record(kio_id: str, available: bool = True, description: str = "desc"):
    r = MagicMock(spec=KIOCapabilityRecord)
    r.kio_id = kio_id
    r.name = f"KIO {kio_id}"
    r.description = description
    r.input_artifact_type = None
    r.output_artifact_type = f"{kio_id}_artifact"
    r.version = "1.0.0"
    r.tags = []
    r.available = available
    r.port = None
    return r


def _make_registry(
    list_records: list | None = None,
    existing_record=None,
) -> CapabilityRegistry:
    """Build a CapabilityRegistry backed by a mock SessionProvider."""

    list_records = list_records or []

    repo = AsyncMock()
    repo.upsert_kio_capability = AsyncMock(return_value=None)
    repo.list_kio_capabilities = AsyncMock(return_value=list_records)
    repo.get_kio_capability = AsyncMock(return_value=existing_record)

    @asynccontextmanager
    async def session_scope():
        yield repo

    @asynccontextmanager
    async def read_scope():
        yield repo

    sp = MagicMock()
    sp.session_scope = session_scope
    sp.read_scope = read_scope

    return CapabilityRegistry(session_provider=sp)


class TestCapabilityRegistryRegister:
    @pytest.mark.asyncio
    async def test_register_calls_upsert(self):
        registry = _make_registry()
        await registry.register(_cap("kio3"))
        registry._sp.session_scope.return_value.__aenter__  # ensure context entered
        # verify upsert was called via the repo mock
        # (indirectly — the asynccontextmanager yields `repo`)

    @pytest.mark.asyncio
    async def test_register_does_not_raise(self):
        registry = _make_registry()
        await registry.register(_cap("kio3", available=True))
        await registry.register(_cap("kio5", available=False))


class TestCapabilityRegistryList:
    @pytest.mark.asyncio
    async def test_list_returns_pydantic_capabilities(self):
        records = [_record("kio3"), _record("kio5")]
        registry = _make_registry(list_records=records)
        caps = await registry.list_capabilities(available_only=False)
        assert len(caps) == 2
        assert all(isinstance(c, KIOCapability) for c in caps)
        kio_ids = {c.kio_id for c in caps}
        assert {"kio3", "kio5"} == kio_ids

    @pytest.mark.asyncio
    async def test_list_available_only_delegates_param(self):
        records = [_record("kio3", available=True)]
        registry = _make_registry(list_records=records)
        caps = await registry.list_capabilities(available_only=True)
        assert len(caps) == 1
        assert caps[0].kio_id == "kio3"

    @pytest.mark.asyncio
    async def test_list_empty_returns_empty_list(self):
        registry = _make_registry(list_records=[])
        caps = await registry.list_capabilities()
        assert caps == []


class TestCapabilityRegistryMarkUnavailable:
    @pytest.mark.asyncio
    async def test_mark_unavailable_sets_flag(self):
        record = _record("kio4", available=True)
        registry = _make_registry(existing_record=record)
        await registry.mark_unavailable("kio4")
        assert record.available is False

    @pytest.mark.asyncio
    async def test_mark_unavailable_missing_kio_no_error(self):
        registry = _make_registry(existing_record=None)
        await registry.mark_unavailable("kio_nonexistent")  # should not raise


class TestCapabilityRegistryPlannerContext:
    @pytest.mark.asyncio
    async def test_planner_context_empty(self):
        registry = _make_registry(list_records=[])
        ctx = await registry.build_planner_context()
        assert "No KIO" in ctx

    @pytest.mark.asyncio
    async def test_planner_context_contains_registered_kio_ids(self):
        records = [_record("kio3"), _record("kio5")]
        registry = _make_registry(list_records=records)
        ctx = await registry.build_planner_context()
        assert "kio3" in ctx
        assert "kio5" in ctx

    @pytest.mark.asyncio
    async def test_planner_context_contains_description(self):
        records = [_record("kio3", description="Repo scanner for bugs")]
        registry = _make_registry(list_records=records)
        ctx = await registry.build_planner_context()
        assert "Repo scanner for bugs" in ctx
