"""S3/MinIO artifact storage layer."""

from .artifact_store import ArtifactStore, get_artifact_store

__all__ = ["ArtifactStore", "get_artifact_store"]
