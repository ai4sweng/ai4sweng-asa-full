"""S3/MinIO artifact storage — Phase 5.

Stores artifact payload JSON in object storage.
When S3_ENABLED=false (default), all operations are no-ops and the DB
stores content directly as before (backward-compatible).

Key format:  artifacts/{workflow_id}/{artifact_id}.json
"""

from __future__ import annotations

import json
from typing import Any

import aiobotocore.session
from loguru import logger

from shared.config import get_settings

_S3_PREFIX = "artifacts"


class ArtifactStore:
    """Async S3/MinIO client for artifact payloads."""

    def __init__(self) -> None:
        cfg = get_settings()
        self._enabled = cfg.s3_enabled
        self._bucket = cfg.s3_bucket
        self._endpoint = cfg.s3_endpoint_url
        self._access_key = cfg.s3_access_key
        self._secret_key = cfg.s3_secret_key
        self._region = cfg.s3_region

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _key(self, workflow_id: str, artifact_id: str) -> str:
        return f"{_S3_PREFIX}/{workflow_id}/{artifact_id}.json"

    def _make_client(self):
        session = aiobotocore.session.get_session()
        return session.create_client(
            "s3",
            region_name=self._region,
            endpoint_url=self._endpoint or None,
            aws_access_key_id=self._access_key or None,
            aws_secret_access_key=self._secret_key or None,
        )

    async def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist (called at startup)."""
        if not self._enabled:
            return
        try:
            async with self._make_client() as s3:
                try:
                    await s3.head_bucket(Bucket=self._bucket)
                    logger.info("[artifact_store] bucket '{}' already exists", self._bucket)
                except s3.exceptions.ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code", "")
                    if code in ("404", "NoSuchBucket"):
                        await s3.create_bucket(Bucket=self._bucket)
                        logger.info("[artifact_store] created bucket '{}'", self._bucket)
                    else:
                        raise
        except Exception as exc:
            logger.warning("[artifact_store] bucket check failed (non-fatal): {}", exc)

    async def put(
        self,
        artifact_id: str,
        workflow_id: str,
        payload: dict[str, Any],
    ) -> str:
        """Upload artifact payload to S3 and return the object key.

        Raises if S3 upload fails — caller should fall back to DB storage.
        """
        key = self._key(workflow_id, artifact_id)
        body = json.dumps(payload, default=str).encode()
        async with self._make_client() as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
        logger.debug("[artifact_store] uploaded {} ({} bytes)", key, len(body))
        return key

    async def get(self, s3_key: str) -> dict[str, Any]:
        """Download and deserialize an artifact payload from S3."""
        async with self._make_client() as s3:
            resp = await s3.get_object(Bucket=self._bucket, Key=s3_key)
            body = await resp["Body"].read()
        return json.loads(body)

    async def delete(self, s3_key: str) -> None:
        """Remove an artifact object from S3 (used by compensation engine)."""
        try:
            async with self._make_client() as s3:
                await s3.delete_object(Bucket=self._bucket, Key=s3_key)
            logger.debug("[artifact_store] deleted {}", s3_key)
        except Exception as exc:
            logger.warning("[artifact_store] delete {} failed: {}", s3_key, exc)

    async def presigned_url(self, s3_key: str, expires_in: int = 3600) -> str:
        """Generate a pre-signed GET URL for dashboard/browser downloads."""
        async with self._make_client() as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": s3_key},
                ExpiresIn=expires_in,
            )
        return url


# ── Singleton ──────────────────────────────────────────────────────────────────

_store: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    global _store
    if _store is None:
        _store = ArtifactStore()
    return _store
