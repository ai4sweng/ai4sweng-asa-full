"""
KIO Capability Registry — runtime discovery for the LLM planner.

KIO services call register() on startup.
The LLM planner calls list_capabilities() to build its prompt context.
"""

from __future__ import annotations

import logging

from shared.contracts.capabilities import KIOCapability
from shared.persistence.session_provider import SessionProvider

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """Thin wrapper around the PostgreSQL KIOCapabilityRecord table."""

    def __init__(self, session_provider: SessionProvider) -> None:
        self._sp = session_provider

    async def register(self, capability: KIOCapability) -> None:
        """Register or update a KIO capability. Called on KIO startup."""
        async with self._sp.session_scope() as repo:
            await repo.upsert_kio_capability(
                kio_id=capability.kio_id,
                name=capability.name,
                description=capability.description,
                output_artifact_type=capability.output_artifact_type,
                input_artifact_type=capability.input_artifact_type,
                version=capability.version,
                tags=capability.tags,
                available=capability.available,
                port=capability.port,
            )
        logger.info("KIO capability registered: %s (%s)", capability.kio_id, capability.name)

    async def mark_unavailable(self, kio_id: str) -> None:
        """Mark a KIO as unavailable (e.g. on health check failure)."""
        async with self._sp.session_scope() as repo:
            record = await repo.get_kio_capability(kio_id)
            if record:
                record.available = False
        logger.warning("KIO marked unavailable: %s", kio_id)

    async def list_capabilities(self, available_only: bool = True) -> list[KIOCapability]:
        """Return all registered KIO capabilities as Pydantic schemas."""
        async with self._sp.read_scope() as repo:
            records = await repo.list_kio_capabilities(available_only=available_only)
        return [
            KIOCapability(
                kio_id=r.kio_id,
                name=r.name,
                description=r.description,
                input_artifact_type=r.input_artifact_type,
                output_artifact_type=r.output_artifact_type,
                version=r.version,
                tags=r.tags or [],
                available=r.available,
                port=r.port,
            )
            for r in records
        ]

    async def build_planner_context(self) -> str:
        """Format capabilities as a prompt block for the LLM planner."""
        capabilities = await self.list_capabilities(available_only=True)
        if not capabilities:
            return "No KIO capabilities registered."
        lines = ["Available KIO services:"]
        for cap in capabilities:
            tags = f" [{', '.join(cap.tags)}]" if cap.tags else ""
            lines.append(
                f"- {cap.kio_id}: {cap.name}{tags}\n"
                f"  Input: {cap.input_artifact_type or 'none'} → "
                f"Output: {cap.output_artifact_type}\n"
                f"  {cap.description}"
            )
        return "\n".join(lines)


# FastAPI dependency helper
_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        from shared.persistence.session_provider import SessionProvider

        _registry = CapabilityRegistry(SessionProvider())
    return _registry
