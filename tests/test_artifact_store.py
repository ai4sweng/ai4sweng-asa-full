"""Unit tests for ArtifactStore (shared/storage/artifact_store.py)."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_store(enabled: bool = True, bucket: str = "test-bucket"):
    from shared.storage.artifact_store import ArtifactStore
    store = ArtifactStore.__new__(ArtifactStore)
    store._enabled = enabled
    store._bucket = bucket
    store._endpoint = "http://localhost:9000"
    store._access_key = "minioadmin"
    store._secret_key = "minioadmin"
    store._region = "us-east-1"
    return store


def _mock_s3_client(store):
    """Return a mock async context manager that acts as the S3 client."""
    mock_client = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    store._make_client = MagicMock(return_value=mock_cm)
    return mock_client


# ── enabled property ──────────────────────────────────────────────────────────

def test_enabled_false_when_s3_disabled():
    store = _make_store(enabled=False)
    assert store.enabled is False


def test_enabled_true_when_s3_enabled():
    store = _make_store(enabled=True)
    assert store.enabled is True


# ── key generation ────────────────────────────────────────────────────────────

def test_key_format():
    store = _make_store()
    key = store._key("wf123", "art456")
    assert key == "artifacts/wf123/art456.json"


# ── ensure_bucket ─────────────────────────────────────────────────────────────

async def test_ensure_bucket_noop_when_disabled():
    store = _make_store(enabled=False)
    store._make_client = MagicMock()
    await store.ensure_bucket()
    store._make_client.assert_not_called()


async def test_ensure_bucket_creates_when_missing():
    from botocore.exceptions import ClientError as BotoCoreClientError

    store = _make_store()
    s3 = _mock_s3_client(store)

    error_resp = {"Error": {"Code": "404"}, "ResponseMetadata": {}}
    s3.head_bucket.side_effect = BotoCoreClientError(error_resp, "HeadBucket")
    # Wire the exception class so the except clause in ensure_bucket resolves it
    s3.exceptions.ClientError = BotoCoreClientError
    s3.create_bucket.return_value = {}

    await store.ensure_bucket()
    s3.create_bucket.assert_awaited_once_with(Bucket="test-bucket")


async def test_ensure_bucket_skips_when_existing():
    store = _make_store()
    s3 = _mock_s3_client(store)
    s3.head_bucket.return_value = {}  # bucket exists

    await store.ensure_bucket()
    s3.create_bucket.assert_not_awaited()


# ── put ───────────────────────────────────────────────────────────────────────

async def test_put_uploads_json_and_returns_key():
    store = _make_store()
    s3 = _mock_s3_client(store)
    s3.put_object.return_value = {}

    key = await store.put("art1", "wf1", {"foo": "bar"})

    assert key == "artifacts/wf1/art1.json"
    s3.put_object.assert_awaited_once()
    call_kwargs = s3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Key"] == "artifacts/wf1/art1.json"
    assert call_kwargs["ContentType"] == "application/json"
    body = json.loads(call_kwargs["Body"])
    assert body == {"foo": "bar"}


async def test_put_propagates_s3_error():
    store = _make_store()
    s3 = _mock_s3_client(store)
    s3.put_object.side_effect = RuntimeError("connection refused")

    with pytest.raises(RuntimeError):
        await store.put("art1", "wf1", {"x": 1})


# ── get ───────────────────────────────────────────────────────────────────────

async def test_get_downloads_and_parses_json():
    store = _make_store()
    s3 = _mock_s3_client(store)

    body_mock = AsyncMock()
    body_mock.read.return_value = json.dumps({"result": 42}).encode()
    s3.get_object.return_value = {"Body": body_mock}

    data = await store.get("artifacts/wf1/art1.json")
    assert data == {"result": 42}
    s3.get_object.assert_awaited_once_with(
        Bucket="test-bucket", Key="artifacts/wf1/art1.json"
    )


# ── delete ────────────────────────────────────────────────────────────────────

async def test_delete_calls_s3_delete_object():
    store = _make_store()
    s3 = _mock_s3_client(store)
    s3.delete_object.return_value = {}

    await store.delete("artifacts/wf1/art1.json")
    s3.delete_object.assert_awaited_once_with(
        Bucket="test-bucket", Key="artifacts/wf1/art1.json"
    )


async def test_delete_swallows_s3_errors():
    store = _make_store()
    s3 = _mock_s3_client(store)
    s3.delete_object.side_effect = RuntimeError("s3 gone")

    await store.delete("artifacts/wf1/art1.json")  # should not raise


# ── presigned_url ─────────────────────────────────────────────────────────────

async def test_presigned_url_returns_url():
    store = _make_store()
    s3 = _mock_s3_client(store)
    s3.generate_presigned_url.return_value = "https://minio/presigned/url"

    url = await store.presigned_url("artifacts/wf1/art1.json", expires_in=600)

    assert url == "https://minio/presigned/url"
    s3.generate_presigned_url.assert_awaited_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": "artifacts/wf1/art1.json"},
        ExpiresIn=600,
    )


# ── singleton ─────────────────────────────────────────────────────────────────

def test_get_artifact_store_returns_same_instance():
    from shared.storage.artifact_store import get_artifact_store
    import shared.storage.artifact_store as module
    module._store = None  # reset singleton

    with patch("shared.storage.artifact_store.get_settings") as mock_cfg:
        s = MagicMock()
        s.s3_enabled = False
        s.s3_endpoint_url = ""
        s.s3_access_key = ""
        s.s3_secret_key = ""
        s.s3_bucket = "b"
        s.s3_region = "us-east-1"
        mock_cfg.return_value = s

        a = get_artifact_store()
        b = get_artifact_store()
    assert a is b
    module._store = None  # clean up
