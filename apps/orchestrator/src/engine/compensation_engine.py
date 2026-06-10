"""Compensation Engine — Slide 17/18 of the interface spec.

On workflow FAILED, walks completed artifacts in reverse order and attempts
to undo each one:
  1. Delete the artifact payload from S3 (if S3_ENABLED and s3_key is set).
  2. Call DELETE /artifacts/{artifact_id} on the producing KIO shell
     (idempotent best-effort; 404 is treated as already-compensated).

States emitted during compensation:
  COMPENSATING   → session status while rolling back
  COMPENSATED    → session status after all steps attempted

SSE events:
  WORKFLOW_COMPENSATING  (once at start)
  STEP_COMPENSATED       (once per artifact, even on partial failure)
  WORKFLOW_COMPENSATED   (once at end)

Compensation is best-effort — a failure on one step never blocks the rest.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
from loguru import logger

from shared.config import get_settings
from shared.storage import get_artifact_store


class CompensationEngine:
    """Reverses completed pipeline steps after a workflow failure."""

    def __init__(self, session_client, emit_fn: Callable[[str, str, str, dict], None]) -> None:
        self._sm = session_client
        self._emit = emit_fn

    async def compensate(
        self,
        session_id: str,
        artifact_ids: list[str],
    ) -> None:
        """Attempt to roll back all registered artifacts for a session.

        Parameters
        ----------
        session_id:   The failed session.
        artifact_ids: Ordered list of artifact IDs registered during the run.
                      Compensation walks this in reverse.
        """
        if not artifact_ids:
            logger.info("[compensation] {} — no artifacts to compensate", session_id[:8])
            return

        logger.info(
            "[compensation] {} — compensating {} artifact(s) in reverse",
            session_id[:8],
            len(artifact_ids),
        )

        # 1. Update session to COMPENSATING
        try:
            await self._sm.update_status(session_id, "COMPENSATING")
        except Exception as exc:
            logger.warning("[compensation] could not set COMPENSATING status: {}", exc)

        self._emit(
            "WORKFLOW_COMPENSATING",
            session_id,
            f"Rolling back {len(artifact_ids)} artifact(s)…",
            {"session_id": session_id, "artifact_count": len(artifact_ids)},
        )

        # 2. Fetch artifact metadata to get s3_key + producer_kio
        try:
            all_artifacts: list[dict[str, Any]] = await self._sm.get_artifacts(session_id)
        except Exception as exc:
            logger.warning("[compensation] could not fetch artifact metadata: {}", exc)
            all_artifacts = []
        artifact_map: dict[str, dict[str, Any]] = {a["artifact_id"]: a for a in all_artifacts}

        # 3. Walk in reverse
        for artifact_id in reversed(artifact_ids):
            await self._compensate_one(session_id, artifact_id, artifact_map.get(artifact_id, {}))

        # 4. Update session to COMPENSATED
        try:
            await self._sm.update_status(session_id, "COMPENSATED")
        except Exception as exc:
            logger.warning("[compensation] could not set COMPENSATED status: {}", exc)

        self._emit(
            "WORKFLOW_COMPENSATED",
            session_id,
            "Compensation complete — all artifacts rolled back.",
            {"session_id": session_id},
        )
        logger.info("[compensation] {} — done", session_id[:8])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _compensate_one(
        self,
        session_id: str,
        artifact_id: str,
        artifact_meta: dict[str, Any],
    ) -> None:
        """Best-effort compensation for a single artifact."""
        producer_kio = artifact_meta.get("producer_kio", "")
        s3_key = artifact_meta.get("s3_key")

        errors: list[str] = []

        # a) Delete from S3
        if s3_key:
            try:
                await get_artifact_store().delete(s3_key)
                logger.debug("[compensation] deleted S3 object {}", s3_key)
            except Exception as exc:
                errors.append(f"s3_delete={exc}")
                logger.warning("[compensation] S3 delete failed for {}: {}", s3_key, exc)

        # b) Notify producing KIO (idempotent; 404 = already gone)
        if producer_kio:
            await self._call_kio_delete(producer_kio, artifact_id, errors)

        status = "ok" if not errors else f"partial ({'; '.join(errors)})"
        self._emit(
            "STEP_COMPENSATED",
            session_id,
            f"Artifact {artifact_id[:8]}… rolled back ({producer_kio or 'unknown'}) — {status}",
            {
                "artifact_id": artifact_id,
                "producer_kio": producer_kio,
                "s3_key": s3_key,
                "status": status,
            },
        )
        logger.info(
            "[compensation] artifact {} ({}) — {}",
            artifact_id[:8],
            producer_kio,
            status,
        )

    async def _call_kio_delete(self, kio_id: str, artifact_id: str, errors: list[str]) -> None:
        """Call DELETE /artifacts/{artifact_id} on the KIO shell (best-effort)."""
        cfg = get_settings()
        host = cfg.kio_base_host or "localhost"
        port = cfg.kio_port_map.get(kio_id, 8000)
        url = f"http://{host}:{port}/artifacts/{artifact_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(url)
                if resp.status_code not in (200, 204, 404):
                    errors.append(f"kio_delete={resp.status_code}")
                    logger.warning(
                        "[compensation] {} DELETE {} → {}",
                        kio_id,
                        artifact_id[:8],
                        resp.status_code,
                    )
                else:
                    logger.debug(
                        "[compensation] {} DELETE {} → {}",
                        kio_id,
                        artifact_id[:8],
                        resp.status_code,
                    )
        except Exception as exc:
            errors.append(f"kio_delete={exc}")
            logger.warning(
                "[compensation] could not reach {} to delete {}: {}", kio_id, artifact_id[:8], exc
            )


# ── Singleton ──────────────────────────────────────────────────────────────────

_engine: CompensationEngine | None = None


def get_compensation_engine(session_client=None, emit_fn=None) -> CompensationEngine:
    """Return the process-wide CompensationEngine singleton.

    Pass session_client and emit_fn on first call; subsequent calls return the
    cached instance regardless of arguments.
    """
    global _engine
    if _engine is None:
        if session_client is None or emit_fn is None:
            raise RuntimeError(
                "CompensationEngine not yet initialised — pass session_client and emit_fn"
            )
        _engine = CompensationEngine(session_client, emit_fn)
    return _engine


def reset_compensation_engine() -> None:
    """Clear the singleton (for testing)."""
    global _engine
    _engine = None
