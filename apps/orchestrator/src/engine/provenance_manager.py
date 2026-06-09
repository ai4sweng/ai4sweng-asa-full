"""Provenance Manager — Slide 17 of the interface spec.

Formal query layer for artifact lineage.  Each KIO step registers its output
artifact with a ``parent_artifact_id`` pointing to the previous step's artifact,
forming a directed acyclic chain.  ProvenanceManager walks that chain and returns
it as an ordered list (root → leaf) for display, audit, or replay.

After ID-unification (Phase 4.4) the artifact_id == DB primary key, so the chain
can be walked with direct lookups rather than content-JSON searches.

Usage (from an orchestrator request handler)::

    pm = get_provenance_manager()
    chain = await pm.get_lineage(session_id, artifact_id)
    # chain[0] is the root (first KIO's output), chain[-1] is the requested artifact.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from ..services.session_client import SessionClient

_MAX_CHAIN_DEPTH = 50  # guard against circular references in corrupt data


class ProvenanceManager:
    """Read-only lineage query layer over Session Manager artifact records."""

    def __init__(self, session_client: "SessionClient") -> None:
        self._sm = session_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_lineage(self, session_id: str, artifact_id: str) -> list[dict[str, Any]]:
        """Walk the parent_artifact_id chain upwards and return the full lineage.

        Returns a list ordered root → ``artifact_id`` (oldest first).
        If an artifact in the chain is missing (e.g. FK not yet populated for
        pre-4.4 rows), the walk stops and the partial chain is returned.

        Parameters
        ----------
        session_id:
            Owning session — used to scope the artifact lookup.
        artifact_id:
            The leaf artifact to start from (KIO-generated UUID == DB PK after 4.4).
        """
        chain: list[dict[str, Any]] = []
        visited: set[str] = set()
        current_id: str | None = artifact_id

        while current_id and len(chain) < _MAX_CHAIN_DEPTH:
            if current_id in visited:
                logger.warning(
                    "[provenance] Cycle detected at artifact {} in session {}",
                    current_id[:8], session_id[:8],
                )
                break
            visited.add(current_id)

            artifact = await self._sm.get_artifact(session_id, current_id)
            if not artifact:
                logger.debug(
                    "[provenance] Artifact {} not found for session {} — chain truncated",
                    current_id[:8], session_id[:8],
                )
                break

            chain.append(artifact)
            current_id = artifact.get("parent_artifact_id")

        chain.reverse()  # root (earliest) → leaf (requested artifact)
        return chain

    async def get_full_lineage(self, session_id: str) -> list[dict[str, Any]]:
        """Return all artifacts for a session in chronological order.

        This is equivalent to walking every leaf node, but faster: it fetches
        all artifacts in one call and returns them sorted by creation time.
        The client can reconstruct the tree from ``parent_artifact_id`` fields.
        """
        artifacts = await self._sm.get_artifacts(session_id)
        # Already ordered by created_at from the repository query
        return artifacts


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_pm: ProvenanceManager | None = None


def get_provenance_manager() -> ProvenanceManager:
    global _pm
    if _pm is None:
        from ..services.session_client import get_session_client
        _pm = ProvenanceManager(get_session_client())
    return _pm
