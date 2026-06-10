"""Tests for artifact content endpoints and SessionReader client — Phase 12 extension.

Covers:
  GET  /sessions/{sid}/artifacts/{aid}/content
  POST /sessions/{sid}/artifacts/batch
  SessionReader.get_artifact_content()
  SessionReader.get_artifacts_batch()
  make_session_reader()
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "session_manager"))
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "kio_shells"))

from src.api.router import router as sessions_router  # noqa: E402
from kio_base import MessageEnvelope, SessionReader, make_session_reader  # noqa: E402

_test_app = FastAPI()
_test_app.include_router(sessions_router)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sid() -> str:
    return str(uuid.uuid4())


def _aid() -> str:
    return str(uuid.uuid4())


def _content_dict(session_id: str, artifact_id: str, producer: str = "kio3") -> dict:
    return {
        "artifact_id": artifact_id,
        "session_id": session_id,
        "producer_kio": producer,
        "artifact_type": "json",
        "artifact_data": {"findings": [{"line": 42, "kind": "sql_injection"}], "kio": producer},
        "parent_artifact_id": None,
    }


@pytest.fixture
def mock_svc():
    svc = AsyncMock()
    svc.get_artifact_content.return_value = None  # default: not found
    svc.get_artifacts_batch.return_value = []  # default: empty
    return svc


@pytest.fixture
def client(mock_svc):
    with patch("src.api.router.get_session_service", return_value=mock_svc):
        yield AsyncClient(transport=ASGITransport(app=_test_app), base_url="http://test")


# ── GET /sessions/{sid}/artifacts/{aid}/content ───────────────────────────────


async def test_content_returns_200_when_found(client, mock_svc):
    sid, aid = _sid(), _aid()
    mock_svc.get_artifact_content.return_value = _content_dict(sid, aid)
    resp = await client.get(f"/sessions/{sid}/artifacts/{aid}/content")
    assert resp.status_code == 200


async def test_content_returns_artifact_data(client, mock_svc):
    sid, aid = _sid(), _aid()
    mock_svc.get_artifact_content.return_value = _content_dict(sid, aid, producer="kio5")
    resp = await client.get(f"/sessions/{sid}/artifacts/{aid}/content")
    body = resp.json()
    assert "artifact_data" in body
    assert body["producer_kio"] == "kio5"


async def test_content_returns_404_when_not_found(client, mock_svc):
    sid, aid = _sid(), _aid()
    mock_svc.get_artifact_content.return_value = None
    resp = await client.get(f"/sessions/{sid}/artifacts/{aid}/content")
    assert resp.status_code == 404


async def test_content_calls_service_with_both_ids(client, mock_svc):
    sid, aid = _sid(), _aid()
    mock_svc.get_artifact_content.return_value = _content_dict(sid, aid)
    await client.get(f"/sessions/{sid}/artifacts/{aid}/content")
    mock_svc.get_artifact_content.assert_awaited_once_with(sid, aid)


async def test_content_returns_404_for_cross_session_artifact(client, mock_svc):
    """Service returns None when artifact belongs to a different session."""
    sid, aid = _sid(), _aid()
    mock_svc.get_artifact_content.return_value = None  # service enforces session scope
    resp = await client.get(f"/sessions/{sid}/artifacts/{aid}/content")
    assert resp.status_code == 404


async def test_content_artifact_data_is_full_payload(client, mock_svc):
    """artifact_data should contain the real KIO output dict, not just metadata."""
    sid, aid = _sid(), _aid()
    rich_data = {"findings": [{"file": "app.py", "line": 10}], "bug_count": 1}
    content = _content_dict(sid, aid)
    content["artifact_data"] = rich_data
    mock_svc.get_artifact_content.return_value = content
    resp = await client.get(f"/sessions/{sid}/artifacts/{aid}/content")
    assert resp.json()["artifact_data"] == rich_data


# ── POST /sessions/{sid}/artifacts/batch ─────────────────────────────────────


async def test_batch_returns_200(client, mock_svc):
    sid = _sid()
    aids = [_aid(), _aid()]
    mock_svc.get_artifacts_batch.return_value = [_content_dict(sid, a) for a in aids]
    resp = await client.post(f"/sessions/{sid}/artifacts/batch", json={"artifact_ids": aids})
    assert resp.status_code == 200


async def test_batch_returns_list(client, mock_svc):
    sid = _sid()
    aids = [_aid(), _aid(), _aid()]
    mock_svc.get_artifacts_batch.return_value = [_content_dict(sid, a) for a in aids]
    resp = await client.post(f"/sessions/{sid}/artifacts/batch", json={"artifact_ids": aids})
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 3


async def test_batch_calls_service_with_ids(client, mock_svc):
    sid = _sid()
    aids = [_aid(), _aid()]
    mock_svc.get_artifacts_batch.return_value = []
    await client.post(f"/sessions/{sid}/artifacts/batch", json={"artifact_ids": aids})
    mock_svc.get_artifacts_batch.assert_awaited_once_with(sid, aids)


async def test_batch_empty_ids_returns_empty_list(client, mock_svc):
    sid = _sid()
    mock_svc.get_artifacts_batch.return_value = []
    resp = await client.post(f"/sessions/{sid}/artifacts/batch", json={"artifact_ids": []})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_batch_omits_missing_artifacts(client, mock_svc):
    """Service silently omits IDs that don't exist — caller gets partial result."""
    sid = _sid()
    existing = _aid()
    missing = _aid()
    # Service only returns the existing one
    mock_svc.get_artifacts_batch.return_value = [_content_dict(sid, existing)]
    resp = await client.post(
        f"/sessions/{sid}/artifacts/batch",
        json={"artifact_ids": [existing, missing]},
    )
    assert len(resp.json()) == 1
    assert resp.json()[0]["artifact_id"] == existing


async def test_batch_producer_kio_preserved(client, mock_svc):
    sid = _sid()
    aids = [_aid(), _aid()]
    results = [
        _content_dict(sid, aids[0], producer="kio3"),
        _content_dict(sid, aids[1], producer="kio5"),
    ]
    mock_svc.get_artifacts_batch.return_value = results
    resp = await client.post(f"/sessions/{sid}/artifacts/batch", json={"artifact_ids": aids})
    producers = {a["producer_kio"] for a in resp.json()}
    assert producers == {"kio3", "kio5"}


# ── Service: get_artifact_content ────────────────────────────────────────────


async def test_service_get_artifact_content_returns_none_for_wrong_session():
    """Service must not return an artifact that belongs to a different session."""
    from src.service.session_service import SessionService

    sid_a = _sid()
    sid_b = _sid()
    aid = _aid()

    # Build a mock artifact that belongs to sid_a
    mock_artifact = MagicMock()
    mock_artifact.id = aid
    mock_artifact.workflow_id = sid_a  # belongs to sid_a
    mock_artifact.kio_id = "kio3"
    mock_artifact.artifact_type = "json"
    mock_artifact.content = {"data": {"result": "ok"}, "state": "CREATED"}
    mock_artifact.s3_key = None
    mock_artifact.parent_artifact_id = None
    mock_artifact.created_at = None

    repo = AsyncMock()
    repo.get_artifact.return_value = mock_artifact

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def read_scope():
        yield repo

    sp = MagicMock()
    sp.read_scope = read_scope

    svc = SessionService(session_provider=sp)
    # Request with sid_b — must return None (cross-session)
    result = await svc.get_artifact_content(sid_b, aid)
    assert result is None


async def test_service_get_artifact_content_returns_data_for_correct_session():
    from src.service.session_service import SessionService

    sid = _sid()
    aid = _aid()

    mock_artifact = MagicMock()
    mock_artifact.id = aid
    mock_artifact.workflow_id = sid
    mock_artifact.kio_id = "kio3"
    mock_artifact.artifact_type = "json"
    mock_artifact.content = {"data": {"bugs": []}, "state": "CREATED"}
    mock_artifact.s3_key = None
    mock_artifact.parent_artifact_id = None

    repo = AsyncMock()
    repo.get_artifact.return_value = mock_artifact

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def read_scope():
        yield repo

    sp = MagicMock()
    sp.read_scope = read_scope

    svc = SessionService(session_provider=sp)
    result = await svc.get_artifact_content(sid, aid)

    assert result is not None
    assert result["artifact_id"] == aid
    assert result["session_id"] == sid
    assert "artifact_data" in result


async def test_service_get_artifacts_batch_concurrent():
    """get_artifacts_batch runs concurrently and returns all found artifacts."""
    from src.service.session_service import SessionService

    sid = _sid()
    aid_a, aid_b = _aid(), _aid()

    def _make_artifact(aid: str):
        a = MagicMock()
        a.id = aid
        a.workflow_id = sid
        a.kio_id = "kio3"
        a.artifact_type = "json"
        a.content = {"data": {}, "state": "CREATED"}
        a.s3_key = None
        a.parent_artifact_id = None
        return a

    artifacts = {aid_a: _make_artifact(aid_a), aid_b: _make_artifact(aid_b)}

    repo = AsyncMock()
    repo.get_artifact.side_effect = lambda aid: artifacts.get(aid)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def read_scope():
        yield repo

    sp = MagicMock()
    sp.read_scope = read_scope

    svc = SessionService(session_provider=sp)
    results = await svc.get_artifacts_batch(sid, [aid_a, aid_b])

    assert len(results) == 2
    found_ids = {r["artifact_id"] for r in results}
    assert found_ids == {aid_a, aid_b}


async def test_service_get_artifacts_batch_empty_input():
    from src.service.session_service import SessionService
    from contextlib import asynccontextmanager

    repo = AsyncMock()

    @asynccontextmanager
    async def read_scope():
        yield repo

    sp = MagicMock()
    sp.read_scope = read_scope
    svc = SessionService(session_provider=sp)

    results = await svc.get_artifacts_batch(_sid(), [])
    assert results == []
    repo.get_artifact.assert_not_awaited()


# ── SessionReader client ──────────────────────────────────────────────────────


async def test_session_reader_get_artifact_content_calls_correct_url():
    sid, aid = _sid(), _aid()
    expected_data = {"artifact_id": aid, "session_id": sid, "producer_kio": "kio3",
                     "artifact_type": "json", "artifact_data": {}, "parent_artifact_id": None}

    import respx
    import httpx

    with respx.mock:
        route = respx.get(
            f"http://sm-test/sessions/{sid}/artifacts/{aid}/content"
        ).mock(return_value=httpx.Response(200, json=expected_data))

        reader = SessionReader("http://sm-test", sid)
        result = await reader.get_artifact_content(aid)

    assert route.called
    assert result["artifact_id"] == aid


async def test_session_reader_returns_empty_dict_on_404():
    sid, aid = _sid(), _aid()

    import respx
    import httpx

    with respx.mock:
        respx.get(
            f"http://sm-test/sessions/{sid}/artifacts/{aid}/content"
        ).mock(return_value=httpx.Response(404, json={"detail": "not found"}))

        reader = SessionReader("http://sm-test", sid)
        result = await reader.get_artifact_content(aid)

    assert result == {}


async def test_session_reader_get_artifacts_batch_calls_correct_url():
    sid = _sid()
    aids = [_aid(), _aid()]
    expected = [
        {"artifact_id": aids[0], "session_id": sid, "producer_kio": "kio3",
         "artifact_type": "json", "artifact_data": {}, "parent_artifact_id": None},
        {"artifact_id": aids[1], "session_id": sid, "producer_kio": "kio5",
         "artifact_type": "json", "artifact_data": {}, "parent_artifact_id": None},
    ]

    import respx
    import httpx

    with respx.mock:
        route = respx.post(
            f"http://sm-test/sessions/{sid}/artifacts/batch"
        ).mock(return_value=httpx.Response(200, json=expected))

        reader = SessionReader("http://sm-test", sid)
        result = await reader.get_artifacts_batch(aids)

    assert route.called
    assert len(result) == 2


async def test_session_reader_batch_returns_empty_list_for_no_ids():
    reader = SessionReader("http://sm-test", _sid())
    result = await reader.get_artifacts_batch([])
    assert result == []


async def test_session_reader_returns_empty_on_network_error():
    sid, aid = _sid(), _aid()

    import respx
    import httpx

    with respx.mock:
        respx.get(
            f"http://sm-test/sessions/{sid}/artifacts/{aid}/content"
        ).mock(side_effect=httpx.ConnectError("refused"))

        reader = SessionReader("http://sm-test", sid)
        result = await reader.get_artifact_content(aid)

    assert result == {}


async def test_make_session_reader_uses_config_url():
    envelope = MessageEnvelope(
        message_id=str(uuid.uuid4()),
        session_id="test-session",
        workflow_id="test-wf",
        source="orchestrator",
        target="kio3",
        timestamp="2026-06-10T00:00:00+00:00",
        message_type="JOB_REQUEST",
    )

    mock_cfg = MagicMock()
    mock_cfg.session_manager_url = "http://session-manager:8002"

    with patch("shared.config.get_settings", return_value=mock_cfg):
        reader = make_session_reader(envelope)

    assert reader._base_url == "http://session-manager:8002"
    assert reader._session_id == "test-session"
