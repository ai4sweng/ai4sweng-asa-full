"""Tests for ProvenanceManager lineage queries — Phase 9.3.5."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from apps.orchestrator.src.engine.provenance_manager import ProvenanceManager


# ── Helpers ───────────────────────────────────────────────────────────────────

def _artifact(
    artifact_id: str,
    parent_artifact_id: str | None = None,
    kio: str = "kio3",
    step: int = 1,
) -> dict:
    return {
        "artifact_id": artifact_id,
        "parent_artifact_id": parent_artifact_id,
        "producer_kio": kio,
        "workflow_stage": f"step_{step}_{kio}",
        "artifact_type": "json",
        "artifact_data": {"kio": kio},
        "state": "CREATED",
    }


def _make_pm(artifacts_by_id: dict[str, dict]) -> ProvenanceManager:
    """Return a ProvenanceManager with a mock SM backed by the given artifact map."""
    sm = AsyncMock()

    async def get_artifact(session_id: str, artifact_id: str):
        return artifacts_by_id.get(artifact_id)

    async def get_artifacts(session_id: str):
        return list(artifacts_by_id.values())

    sm.get_artifact.side_effect = get_artifact
    sm.get_artifacts.side_effect = get_artifacts

    return ProvenanceManager(session_client=sm)


# ── get_lineage — single artifact (no parent) ─────────────────────────────────

async def test_get_lineage_single_artifact():
    aid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    pm = _make_pm({aid: _artifact(aid, parent_artifact_id=None)})

    chain = await pm.get_lineage(session_id, aid)

    assert len(chain) == 1
    assert chain[0]["artifact_id"] == aid


async def test_get_lineage_single_artifact_root_first():
    """The returned list is always root → leaf; with one item it is also root == leaf."""
    aid = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    pm = _make_pm({aid: _artifact(aid)})

    chain = await pm.get_lineage(session_id, aid)

    assert chain[0]["artifact_id"] == aid  # only element IS the root


# ── get_lineage — chain walking ───────────────────────────────────────────────

async def test_get_lineage_chain_two_levels():
    """B → A should return [A, B] (root first)."""
    a_id = str(uuid.uuid4())
    b_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    pm = _make_pm({
        a_id: _artifact(a_id, parent_artifact_id=None, kio="kio3", step=1),
        b_id: _artifact(b_id, parent_artifact_id=a_id, kio="kio4", step=2),
    })

    chain = await pm.get_lineage(session_id, b_id)

    assert len(chain) == 2
    assert chain[0]["artifact_id"] == a_id   # root
    assert chain[1]["artifact_id"] == b_id   # leaf


async def test_get_lineage_chain_three_levels():
    """C → B → A should return [A, B, C] (root first)."""
    a_id = str(uuid.uuid4())
    b_id = str(uuid.uuid4())
    c_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    pm = _make_pm({
        a_id: _artifact(a_id, parent_artifact_id=None, kio="kio3", step=1),
        b_id: _artifact(b_id, parent_artifact_id=a_id, kio="kio4", step=2),
        c_id: _artifact(c_id, parent_artifact_id=b_id, kio="kio5", step=3),
    })

    chain = await pm.get_lineage(session_id, c_id)

    assert len(chain) == 3
    assert [a["artifact_id"] for a in chain] == [a_id, b_id, c_id]


async def test_get_lineage_chain_order_root_to_leaf():
    """Order must always be root (no parent) → leaf (requested artifact)."""
    ids = [str(uuid.uuid4()) for _ in range(5)]
    session_id = str(uuid.uuid4())

    # Build chain: ids[0] ← ids[1] ← ids[2] ← ids[3] ← ids[4]
    artifacts_map = {}
    for i, aid in enumerate(ids):
        parent = ids[i - 1] if i > 0 else None
        artifacts_map[aid] = _artifact(aid, parent_artifact_id=parent, kio=f"kio{i+2}", step=i + 1)

    pm = _make_pm(artifacts_map)
    chain = await pm.get_lineage(session_id, ids[-1])  # start from leaf

    assert [a["artifact_id"] for a in chain] == ids


# ── get_lineage — missing artifact stops walk ─────────────────────────────────

async def test_get_lineage_missing_artifact_returns_empty():
    session_id = str(uuid.uuid4())
    pm = _make_pm({})  # no artifacts at all

    chain = await pm.get_lineage(session_id, "non-existent-id")

    assert chain == []


async def test_get_lineage_truncated_at_missing_parent():
    """If B claims parent A but A is not in SM, return just [B]."""
    b_id = str(uuid.uuid4())
    missing_a_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    pm = _make_pm({b_id: _artifact(b_id, parent_artifact_id=missing_a_id)})

    chain = await pm.get_lineage(session_id, b_id)

    # B is returned but chain is truncated because A is missing
    assert len(chain) == 1
    assert chain[0]["artifact_id"] == b_id


async def test_get_lineage_partial_chain_is_still_root_first():
    """Even with a truncated chain (missing grandparent), order is root → leaf."""
    a_id = str(uuid.uuid4())
    b_id = str(uuid.uuid4())
    missing_root_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # Chain would be: missing_root → A → B, but missing_root is absent
    pm = _make_pm({
        a_id: _artifact(a_id, parent_artifact_id=missing_root_id),
        b_id: _artifact(b_id, parent_artifact_id=a_id),
    })

    chain = await pm.get_lineage(session_id, b_id)

    # Walk: B → A → (missing_root, stops). Returns [A, B] reversed = [A, B]
    assert chain[0]["artifact_id"] == a_id
    assert chain[1]["artifact_id"] == b_id


# ── get_full_lineage ──────────────────────────────────────────────────────────

async def test_get_full_lineage_returns_all_artifacts():
    session_id = str(uuid.uuid4())
    a_id, b_id, c_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    pm = _make_pm({
        a_id: _artifact(a_id),
        b_id: _artifact(b_id, parent_artifact_id=a_id),
        c_id: _artifact(c_id, parent_artifact_id=b_id),
    })

    artifacts = await pm.get_full_lineage(session_id)

    assert len(artifacts) == 3
    artifact_ids = {a["artifact_id"] for a in artifacts}
    assert artifact_ids == {a_id, b_id, c_id}


async def test_get_full_lineage_empty_session():
    session_id = str(uuid.uuid4())
    pm = _make_pm({})

    artifacts = await pm.get_full_lineage(session_id)

    assert artifacts == []


async def test_get_full_lineage_calls_get_artifacts_once():
    session_id = str(uuid.uuid4())
    sm = AsyncMock()
    sm.get_artifacts.return_value = []
    pm = ProvenanceManager(session_client=sm)

    await pm.get_full_lineage(session_id)

    sm.get_artifacts.assert_awaited_once_with(session_id)


# ── Cycle detection ───────────────────────────────────────────────────────────

async def test_get_lineage_stops_on_cycle():
    """If artifacts reference each other in a cycle, the walk must terminate."""
    a_id = str(uuid.uuid4())
    b_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # A.parent = B, B.parent = A — circular reference
    pm = _make_pm({
        a_id: _artifact(a_id, parent_artifact_id=b_id),
        b_id: _artifact(b_id, parent_artifact_id=a_id),
    })

    # Must not loop forever; result is finite
    chain = await pm.get_lineage(session_id, a_id)
    assert len(chain) <= 50  # bounded by _MAX_CHAIN_DEPTH
