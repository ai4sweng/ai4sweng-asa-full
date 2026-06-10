"""Dynamic agent registry — populated by CAPABILITY_ANNOUNCEMENT messages from KIO shells.

KIOs publish their host/port/capabilities on startup and every 60 s.
KioClient queries this registry before falling back to the static kio_port_map,
so new KIOs are discovered without any config change.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from shared.config import get_settings


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        # Callbacks fired when a kio_id's endpoint changes (used by KioClient to bust cache)
        self._endpoint_change_cbs: list = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def on_endpoint_change(self, cb) -> None:
        """Register a callback(kio_id) called when an agent's host/port changes."""
        self._endpoint_change_cbs.append(cb)

    async def handle_announcement(self, data: dict[str, Any]) -> None:
        """Process a CAPABILITY_ANNOUNCEMENT message from a KIO."""
        kio_id: str = data.get("agent_id", "")
        if not kio_id:
            return

        endpoint = data.get("endpoint", {})
        host = str(endpoint.get("host", ""))
        port = int(endpoint.get("port", 0))

        async with self._lock:
            prev = self._agents.get(kio_id, {})
            prev_endpoint = (prev.get("host"), prev.get("port"))
            new_endpoint = (host, port)

            self._agents[kio_id] = {
                "kio_id": kio_id,
                "version": data.get("version", ""),
                "protocol_version": data.get("protocol_version", ""),
                "host": host,
                "port": port,
                "supported_tasks": data.get("supported_tasks", []),
                "hardware_requirements": data.get("hardware_requirements", {}),
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }

        if prev_endpoint != new_endpoint and host and port:
            logger.info("[agent_registry] {} registered → {}:{}", kio_id, host, port)
            for cb in self._endpoint_change_cbs:
                try:
                    cb(kio_id)
                except Exception:
                    pass
            # Notify orchestrator SM: new or recovered agent
            try:
                from .orchestrator_state import get_orchestrator_sm

                get_orchestrator_sm().agent_registered(kio_id)
            except Exception:
                pass
        else:
            logger.debug("[agent_registry] {} re-announced at {}:{}", kio_id, host, port)
            # Re-announcement also counts as alive (recovery from stale)
            try:
                from .orchestrator_state import get_orchestrator_sm

                sm = get_orchestrator_sm()
                sm.agent_registered(kio_id)
                sm.agent_recovered(kio_id)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_endpoint(self, kio_id: str) -> tuple[str, int] | None:
        """Return (host, port) if the agent is registered and not stale, else None."""
        agent = self._agents.get(kio_id)
        if not agent:
            return None
        try:
            last_seen = datetime.fromisoformat(agent["last_seen"])
            age = (datetime.now(timezone.utc) - last_seen).total_seconds()
            if age > get_settings().agent_stale_threshold:
                logger.warning(
                    "[agent_registry] {} last seen {:.0f}s ago — endpoint stale, using config fallback",
                    kio_id,
                    age,
                )
                # Notify orchestrator SM of agent failure
                if not agent.get("_stale_notified"):
                    agent["_stale_notified"] = True
                    try:
                        from .orchestrator_state import get_orchestrator_sm

                        get_orchestrator_sm().agent_failure_detected(kio_id)
                    except Exception:
                        pass
                return None
        except Exception:
            return None

        # Agent is alive — clear stale flag if it was set
        if agent.get("_stale_notified"):
            agent["_stale_notified"] = False

        host, port = agent.get("host", ""), agent.get("port", 0)
        return (host, port) if host and port else None

    def is_alive(self, kio_id: str) -> bool:
        return self.get_endpoint(kio_id) is not None

    def list_agents(self) -> list[dict[str, Any]]:
        """Return a snapshot of all known agents with liveness info."""
        now = datetime.now(timezone.utc)
        result = []
        for agent in self._agents.values():
            try:
                last_seen = datetime.fromisoformat(agent["last_seen"])
                age = (now - last_seen).total_seconds()
                alive = age <= get_settings().agent_stale_threshold
            except Exception:
                age = -1
                alive = False
            result.append({**agent, "alive": alive, "last_seen_seconds_ago": int(age)})
        return sorted(result, key=lambda a: a["kio_id"])


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry
