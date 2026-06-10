"""Session Manager HTTP integration tests — Phase 12.

Uses httpx.AsyncClient with ASGITransport against the real FastAPI router
mounted on a lightweight test app (no DB, no lifespan). The SessionService
singleton is mocked for every test so no PostgreSQL connection is needed.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Make the session_manager src/ package importable without running main.py
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "session_manager"))

from src.api.router import router as sessions_router  # noqa: E402


# ── Test app ──────────────────────────────────────────────────────────────────

_test_app = FastAPI()
_test_app.include_router(sessions_router)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _session_dict(
    session_id: str | None = None,
    status: str = "ACTIVE",
    owner: str = "test_user",
    workflow_id: str | None = None,
) -> dict:
    sid = session_id or str(uuid.uuid4())
    return {
        "session_id": sid,
        "workflow_id": workflow_id or sid,
        "status": status,
        "owner": owner,
        "metadata": {},
    }


def _artifact_dict(session_id: str, artifact_id: str | None = None) -> dict:
    aid = artifact_id or str(uuid.uuid4())
    return {
        "artifact_id": aid,
        "session_id": session_id,
        "producer_kio": "kio3",
        "artifact_type": "json",
        "artifact_data": {"kio": "kio3", "result": "ok"},
        "state": "CREATED",
        "parent_artifact_id": None,
        "created_at": "2026-06-10T00:00:00+00:00",
    }


def _checkpoint_dict(session_id: str, checkpoint_id: str | None = None, state: str = "PENDING") -> dict:
    return {
        "checkpoint_id": checkpoint_id or str(uuid.uuid4()),
        "session_id": session_id,
        "workflow_step": "post_kio5",
        "state": state,
        "decided_by": None,
        "feedback": None,
        "artifact_id": None,
    }


@pytest.fixture
def mock_svc():
    """Return an AsyncMock SessionService with sensible default return values."""
    svc = AsyncMock()
    sid = str(uuid.uuid4())
    svc._default_session_id = sid  # expose for assertions
    svc.create_session.return_value = _session_dict(sid)
    svc.get_session.return_value = _session_dict(sid)
    svc.list_sessions.return_value = [_session_dict(sid)]
    svc.update_status.return_value = _session_dict(sid, status="COMPLETED")
    svc.register_artifact.return_value = _artifact_dict(sid)
    svc.get_artifacts.return_value = [_artifact_dict(sid)]
    svc.get_artifact.return_value = _artifact_dict(sid)
    svc.create_hitl_checkpoint.return_value = _checkpoint_dict(sid)
    svc.resolve_checkpoint.return_value = _checkpoint_dict(sid, state="APPROVED")
    return svc


@pytest.fixture
def client(mock_svc):
    """Return an async httpx client wired to the test app with mocked service."""
    with patch("src.api.router.get_session_service", return_value=mock_svc):
        yield AsyncClient(transport=ASGITransport(app=_test_app), base_url="http://test")


# ── POST /sessions/ ───────────────────────────────────────────────────────────

async def test_create_session_returns_201(client, mock_svc):
    resp = await client.post("/sessions/", json={"owner": "alice", "workflow_id": "wf-001"})
    assert resp.status_code == 201


async def test_create_session_body_has_session_id(client, mock_svc):
    resp = await client.post("/sessions/", json={"owner": "alice"})
    assert "session_id" in resp.json()


async def test_create_session_calls_service(client, mock_svc):
    await client.post("/sessions/", json={"owner": "bob", "workflow_id": "wf-x"})
    mock_svc.create_session.assert_awaited_once()


async def test_create_session_passes_owner(client, mock_svc):
    await client.post("/sessions/", json={"owner": "charlie"})
    call_kwargs = mock_svc.create_session.await_args.kwargs
    assert call_kwargs["owner"] == "charlie"


# ── GET /sessions/ ────────────────────────────────────────────────────────────

async def test_list_sessions_returns_200(client, mock_svc):
    resp = await client.get("/sessions/")
    assert resp.status_code == 200


async def test_list_sessions_returns_array(client, mock_svc):
    resp = await client.get("/sessions/")
    assert isinstance(resp.json(), list)


async def test_list_sessions_owner_filter_passed_through(client, mock_svc):
    await client.get("/sessions/?owner=alice")
    mock_svc.list_sessions.assert_awaited_once_with(owner="alice")


# ── GET /sessions/{id} ───────────────────────────────────────────────────────

async def test_get_session_returns_200_when_found(client, mock_svc):
    resp = await client.get(f"/sessions/{mock_svc._default_session_id}")
    assert resp.status_code == 200


async def test_get_session_returns_404_when_not_found(client, mock_svc):
    mock_svc.get_session.return_value = None
    resp = await client.get("/sessions/non-existent-id")
    assert resp.status_code == 404


async def test_get_session_body_has_session_id(client, mock_svc):
    resp = await client.get(f"/sessions/{mock_svc._default_session_id}")
    assert "session_id" in resp.json()


# ── PUT /sessions/{id}/status ─────────────────────────────────────────────────

async def test_update_status_returns_200(client, mock_svc):
    sid = mock_svc._default_session_id
    resp = await client.put(f"/sessions/{sid}/status", json={"status": "COMPLETED"})
    assert resp.status_code == 200


async def test_update_status_calls_service_with_correct_status(client, mock_svc):
    sid = mock_svc._default_session_id
    await client.put(f"/sessions/{sid}/status", json={"status": "FAILED"})
    call_args = mock_svc.update_status.await_args
    assert call_args.args[1] == "FAILED"


async def test_update_status_returns_404_when_session_missing(client, mock_svc):
    mock_svc.update_status.return_value = None
    resp = await client.put("/sessions/ghost/status", json={"status": "FAILED"})
    assert resp.status_code == 404


async def test_update_status_body_has_status(client, mock_svc):
    sid = mock_svc._default_session_id
    resp = await client.put(f"/sessions/{sid}/status", json={"status": "COMPLETED"})
    assert "status" in resp.json()


# ── POST /sessions/{id}/artifacts ─────────────────────────────────────────────

async def test_register_artifact_returns_201(client, mock_svc):
    sid = mock_svc._default_session_id
    resp = await client.post(f"/sessions/{sid}/artifacts", json={
        "producer_kio": "kio3",
        "artifact_type": "json",
        "artifact_data": {"bugs": []},
    })
    assert resp.status_code == 201


async def test_register_artifact_body_has_artifact_id(client, mock_svc):
    sid = mock_svc._default_session_id
    resp = await client.post(f"/sessions/{sid}/artifacts", json={
        "producer_kio": "kio5",
        "artifact_data": {},
    })
    assert "artifact_id" in resp.json()


async def test_register_artifact_calls_service(client, mock_svc):
    sid = mock_svc._default_session_id
    await client.post(f"/sessions/{sid}/artifacts", json={
        "producer_kio": "kio4",
        "artifact_data": {"tests": []},
    })
    mock_svc.register_artifact.assert_awaited_once()


async def test_register_artifact_passes_producer_kio(client, mock_svc):
    sid = mock_svc._default_session_id
    await client.post(f"/sessions/{sid}/artifacts", json={"producer_kio": "kio8", "artifact_data": {}})
    kwargs = mock_svc.register_artifact.await_args.kwargs
    assert kwargs["producer_kio"] == "kio8"


# ── GET /sessions/{id}/artifacts ─────────────────────────────────────────────

async def test_get_artifacts_returns_200(client, mock_svc):
    sid = mock_svc._default_session_id
    resp = await client.get(f"/sessions/{sid}/artifacts")
    assert resp.status_code == 200


async def test_get_artifacts_returns_list(client, mock_svc):
    sid = mock_svc._default_session_id
    resp = await client.get(f"/sessions/{sid}/artifacts")
    assert isinstance(resp.json(), list)


async def test_get_artifacts_empty_session_returns_empty_list(client, mock_svc):
    mock_svc.get_artifacts.return_value = []
    sid = str(uuid.uuid4())
    resp = await client.get(f"/sessions/{sid}/artifacts")
    assert resp.status_code == 200
    assert resp.json() == []


# ── GET /sessions/{id}/artifacts/{artifact_id} ───────────────────────────────

async def test_get_artifact_by_id_returns_200(client, mock_svc):
    sid = mock_svc._default_session_id
    aid = str(uuid.uuid4())
    mock_svc.get_artifact.return_value = _artifact_dict(sid, artifact_id=aid)
    resp = await client.get(f"/sessions/{sid}/artifacts/{aid}")
    assert resp.status_code == 200


async def test_get_artifact_by_id_returns_404_when_not_found(client, mock_svc):
    mock_svc.get_artifact.return_value = None
    resp = await client.get(f"/sessions/any/artifacts/missing-id")
    assert resp.status_code == 404


async def test_get_artifact_by_id_returns_404_for_wrong_session(client, mock_svc):
    """Artifact exists but belongs to a different session — 404."""
    aid = str(uuid.uuid4())
    # artifact.session_id != the requested session_id
    mock_svc.get_artifact.return_value = _artifact_dict("other-session-id", artifact_id=aid)
    resp = await client.get(f"/sessions/wrong-session/artifacts/{aid}")
    assert resp.status_code == 404


# ── POST /sessions/{id}/hitl ─────────────────────────────────────────────────

async def test_create_hitl_checkpoint_returns_201(client, mock_svc):
    sid = mock_svc._default_session_id
    resp = await client.post(f"/sessions/{sid}/hitl", json={
        "workflow_step": "post_kio5",
        "artifact_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 201


async def test_create_hitl_checkpoint_body_has_checkpoint_id(client, mock_svc):
    sid = mock_svc._default_session_id
    resp = await client.post(f"/sessions/{sid}/hitl", json={"workflow_step": "post_kio5"})
    assert "checkpoint_id" in resp.json()


async def test_create_hitl_checkpoint_calls_service(client, mock_svc):
    sid = mock_svc._default_session_id
    await client.post(f"/sessions/{sid}/hitl", json={
        "workflow_step": "post_kio3",
        "artifact_id": str(uuid.uuid4()),
    })
    mock_svc.create_hitl_checkpoint.assert_awaited_once()


# ── PUT /sessions/{id}/hitl/{checkpoint_id}/resolve → PUT /sessions/{id}/hitl/{ckpt} ──

async def test_resolve_checkpoint_approved_returns_200(client, mock_svc):
    sid = mock_svc._default_session_id
    ckpt = str(uuid.uuid4())
    resp = await client.put(f"/sessions/{sid}/hitl/{ckpt}", json={
        "action": "APPROVED",
        "actor": "dev_lead",
        "feedback": "Looks good.",
    })
    assert resp.status_code == 200


async def test_resolve_checkpoint_rejected_returns_200(client, mock_svc):
    sid = mock_svc._default_session_id
    ckpt = str(uuid.uuid4())
    mock_svc.resolve_checkpoint.return_value = _checkpoint_dict(sid, state="REJECTED")
    resp = await client.put(f"/sessions/{sid}/hitl/{ckpt}", json={
        "action": "REJECTED",
        "actor": "security_officer",
        "feedback": "Critical bugs remain.",
    })
    assert resp.status_code == 200


async def test_resolve_checkpoint_calls_service_with_action(client, mock_svc):
    sid = mock_svc._default_session_id
    ckpt = str(uuid.uuid4())
    await client.put(f"/sessions/{sid}/hitl/{ckpt}", json={"action": "APPROVED", "actor": "ops"})
    kwargs = mock_svc.resolve_checkpoint.await_args.kwargs
    assert kwargs["action"] == "APPROVED"


async def test_resolve_checkpoint_returns_404_for_unknown_checkpoint(client, mock_svc):
    mock_svc.resolve_checkpoint.return_value = None
    resp = await client.put("/sessions/sid/hitl/ghost-ckpt", json={"action": "APPROVED", "actor": "x"})
    assert resp.status_code == 404


async def test_resolve_checkpoint_body_has_state(client, mock_svc):
    sid = mock_svc._default_session_id
    ckpt = str(uuid.uuid4())
    resp = await client.put(f"/sessions/{sid}/hitl/{ckpt}", json={"action": "APPROVED", "actor": "lead"})
    assert "state" in resp.json()
