"""Unit tests for CompensationEngine (compensation_engine.py)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from apps.orchestrator.src.engine.compensation_engine import (
    CompensationEngine,
    reset_compensation_engine,
    get_compensation_engine,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    reset_compensation_engine()
    yield
    reset_compensation_engine()


@pytest.fixture()
def emit_fn():
    return MagicMock()


@pytest.fixture()
def sm():
    mock = AsyncMock()
    mock.update_status = AsyncMock()
    mock.get_artifacts = AsyncMock(return_value=[])
    return mock


@pytest.fixture()
def engine(sm, emit_fn):
    return CompensationEngine(sm, emit_fn)


# ── No artifacts (early-exit) ─────────────────────────────────────────────────

async def test_compensate_empty_list_does_nothing(engine, sm, emit_fn):
    await engine.compensate("sess1", [])
    sm.update_status.assert_not_awaited()
    emit_fn.assert_not_called()


# ── Status transitions ────────────────────────────────────────────────────────

async def test_compensate_sets_compensating_then_compensated(engine, sm, emit_fn):
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store") as mock_store:
        mock_store.return_value.delete = AsyncMock()
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
                delete=AsyncMock(return_value=MagicMock(status_code=204))
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            sm.get_artifacts.return_value = [
                {"artifact_id": "art1", "producer_kio": "kio3", "s3_key": None}
            ]
            await engine.compensate("sess1", ["art1"])

    # update_status is called as update_status(session_id, status_string)
    calls = [c.args[1] for c in sm.update_status.await_args_list]
    assert "COMPENSATING" in calls
    assert "COMPENSATED" in calls


async def test_compensating_status_set_before_compensated(engine, sm, emit_fn):
    order = []
    async def track_status(sid, status, *args, **kwargs):
        order.append(status)
    sm.update_status.side_effect = track_status

    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store") as mock_store:
        mock_store.return_value.delete = AsyncMock()
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
                delete=AsyncMock(return_value=MagicMock(status_code=204))
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            sm.get_artifacts.return_value = [
                {"artifact_id": "art1", "producer_kio": "", "s3_key": None}
            ]
            await engine.compensate("sess1", ["art1"])

    assert order == ["COMPENSATING", "COMPENSATED"]


# ── SSE events ────────────────────────────────────────────────────────────────

async def test_workflow_compensating_event_emitted(engine, sm, emit_fn):
    sm.get_artifacts.return_value = [
        {"artifact_id": "art1", "producer_kio": "", "s3_key": None}
    ]
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store"):
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
                delete=AsyncMock(return_value=MagicMock(status_code=204))
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            await engine.compensate("sess1", ["art1"])

    event_names = [c.args[0] for c in emit_fn.call_args_list]
    assert "WORKFLOW_COMPENSATING" in event_names
    assert "STEP_COMPENSATED" in event_names
    assert "WORKFLOW_COMPENSATED" in event_names


async def test_step_compensated_per_artifact(engine, sm, emit_fn):
    sm.get_artifacts.return_value = [
        {"artifact_id": "art1", "producer_kio": "", "s3_key": None},
        {"artifact_id": "art2", "producer_kio": "", "s3_key": None},
    ]
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store"):
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
                delete=AsyncMock(return_value=MagicMock(status_code=204))
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            await engine.compensate("sess1", ["art1", "art2"])

    step_events = [c for c in emit_fn.call_args_list if c.args[0] == "STEP_COMPENSATED"]
    assert len(step_events) == 2


# ── Reverse order ─────────────────────────────────────────────────────────────

async def test_compensation_walks_artifacts_in_reverse(engine, sm, emit_fn):
    compensated_order = []

    async def track_compensate(session_id, artifact_id, artifact_meta):
        compensated_order.append(artifact_id)

    engine._compensate_one = track_compensate
    sm.get_artifacts.return_value = []

    await engine.compensate("sess1", ["art1", "art2", "art3"])
    assert compensated_order == ["art3", "art2", "art1"]


# ── S3 deletion ───────────────────────────────────────────────────────────────

async def test_s3_delete_called_when_s3_key_present(engine, sm, emit_fn):
    sm.get_artifacts.return_value = [
        {"artifact_id": "art1", "producer_kio": "", "s3_key": "artifacts/wf/art1.json"}
    ]
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store") as mock_store_fn:
        mock_store = MagicMock()
        mock_store.delete = AsyncMock()
        mock_store_fn.return_value = mock_store
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
                delete=AsyncMock(return_value=MagicMock(status_code=204))
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            await engine.compensate("sess1", ["art1"])

    mock_store.delete.assert_awaited_once_with("artifacts/wf/art1.json")


async def test_s3_delete_skipped_when_no_s3_key(engine, sm, emit_fn):
    sm.get_artifacts.return_value = [
        {"artifact_id": "art1", "producer_kio": "", "s3_key": None}
    ]
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store") as mock_store_fn:
        mock_store = MagicMock()
        mock_store.delete = AsyncMock()
        mock_store_fn.return_value = mock_store
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
                delete=AsyncMock(return_value=MagicMock(status_code=204))
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            await engine.compensate("sess1", ["art1"])

    mock_store.delete.assert_not_awaited()


# ── KIO DELETE endpoint ───────────────────────────────────────────────────────

async def test_kio_delete_called_for_known_producer(engine, sm, emit_fn):
    sm.get_artifacts.return_value = [
        {"artifact_id": "art1", "producer_kio": "kio3", "s3_key": None}
    ]
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store") as ms:
        ms.return_value.delete = AsyncMock()
        with patch("httpx.AsyncClient") as mock_http:
            mock_client = AsyncMock()
            mock_client.delete = AsyncMock(return_value=MagicMock(status_code=204))
            mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            await engine.compensate("sess1", ["art1"])

    mock_client.delete.assert_awaited_once()
    url = mock_client.delete.call_args.args[0]
    assert "art1" in url
    assert "/artifacts/" in url


async def test_kio_delete_404_treated_as_ok(engine, sm, emit_fn):
    sm.get_artifacts.return_value = [
        {"artifact_id": "art1", "producer_kio": "kio3", "s3_key": None}
    ]
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store") as ms:
        ms.return_value.delete = AsyncMock()
        with patch("httpx.AsyncClient") as mock_http:
            mock_client = AsyncMock()
            mock_client.delete = AsyncMock(return_value=MagicMock(status_code=404))
            mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            await engine.compensate("sess1", ["art1"])  # should not raise

    event_names = [c.args[0] for c in emit_fn.call_args_list]
    assert "WORKFLOW_COMPENSATED" in event_names  # still completes


async def test_kio_delete_network_error_is_best_effort(engine, sm, emit_fn):
    sm.get_artifacts.return_value = [
        {"artifact_id": "art1", "producer_kio": "kio3", "s3_key": None}
    ]
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store") as ms:
        ms.return_value.delete = AsyncMock()
        with patch("httpx.AsyncClient") as mock_http:
            mock_client = AsyncMock()
            mock_client.delete = AsyncMock(side_effect=Exception("connection refused"))
            mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            await engine.compensate("sess1", ["art1"])  # must not raise

    event_names = [c.args[0] for c in emit_fn.call_args_list]
    assert "WORKFLOW_COMPENSATED" in event_names


# ── Status update failure ─────────────────────────────────────────────────────

async def test_status_update_failure_does_not_abort_compensation(engine, sm, emit_fn):
    sm.update_status.side_effect = Exception("DB down")
    sm.get_artifacts.return_value = [
        {"artifact_id": "art1", "producer_kio": "", "s3_key": None}
    ]
    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store"):
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
                delete=AsyncMock(return_value=MagicMock(status_code=204))
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            await engine.compensate("sess1", ["art1"])  # should not raise

    event_names = [c.args[0] for c in emit_fn.call_args_list]
    assert "WORKFLOW_COMPENSATED" in event_names


# ── Artifact metadata not found ───────────────────────────────────────────────

async def test_unknown_artifact_id_handled_gracefully(engine, sm, emit_fn):
    sm.get_artifacts.return_value = []  # artifact_map will be empty

    with patch("apps.orchestrator.src.engine.compensation_engine.get_artifact_store") as ms:
        ms.return_value.delete = AsyncMock()
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(
                delete=AsyncMock(return_value=MagicMock(status_code=204))
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
            await engine.compensate("sess1", ["unknown-art"])  # no s3_key, no kio

    event_names = [c.args[0] for c in emit_fn.call_args_list]
    assert "STEP_COMPENSATED" in event_names


# ── Singleton ─────────────────────────────────────────────────────────────────

def test_get_compensation_engine_requires_args_on_first_call():
    with pytest.raises(RuntimeError, match="not yet initialised"):
        get_compensation_engine()


def test_get_compensation_engine_returns_same_instance(sm, emit_fn):
    a = get_compensation_engine(session_client=sm, emit_fn=emit_fn)
    b = get_compensation_engine()  # no args — returns cached
    assert a is b
