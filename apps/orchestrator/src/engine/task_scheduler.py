"""Task Scheduler — Slide 17 of the interface spec.

Formal module that decides which KIO to dispatch next.  Previously this logic
lived inline in ``run_kio_node`` as ``kio_id = kio_seq[step]``.  Extracting it
here enables capability-based routing (4.3) and makes dispatch decisions testable
in isolation.

Scheduling algorithm
--------------------
1. Read ``kio_id = kio_sequence[current_step]``.
2. If AgentRegistry has at least one registered agent (NATS active), check
   that ``kio_id`` is alive (announced within ``agent_stale_threshold`` seconds).
3. If alive: check that the agent's ``supported_tasks`` list contains a task
   whose ``task_type`` matches ``kio_id``.  This is intentionally permissive —
   any declared task_type that equals the kio_id string is sufficient.
4. If no alive capable agent is found but the registry is *empty* (NATS not
   connected or agents not yet announced), fall back to config-based dispatch
   so HTTP-mode pipelines are not blocked.
5. Emit ``TASK_NO_CAPABLE_AGENT`` SSE (via caller) and return None if check fails
   with a non-empty registry.

The caller (``run_kio_node``) raises a RuntimeError on None, which feeds into
the RetryManager / HITL fallback chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from .agent_registry import AgentRegistry


class TaskScheduler:
    """Selects the KIO to dispatch for the current pipeline step."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule(
        self,
        kio_sequence: list[str],
        current_step: int,
        agent_registry: "AgentRegistry",
    ) -> str | None:
        """Return the kio_id to dispatch, or None if no capable agent is available.

        Parameters
        ----------
        kio_sequence:
            Ordered list of KIO IDs for this workflow.
        current_step:
            Zero-based index of the step to dispatch.
        agent_registry:
            Live in-process registry populated by CAPABILITY_ANNOUNCEMENT messages.

        Returns
        -------
        str | None
            The kio_id to dispatch, or None if capability check failed.
        """
        if current_step >= len(kio_sequence):
            logger.error(
                "[task_scheduler] step {} out of range (seq len {})",
                current_step,
                len(kio_sequence),
            )
            return None

        kio_id = kio_sequence[current_step]

        # If the registry is empty (HTTP mode / NATS not active / no agents yet),
        # skip capability check to avoid blocking config-driven pipelines.
        registered = agent_registry._agents
        if not registered:
            logger.debug(
                "[task_scheduler] registry empty — skipping capability check for {}", kio_id
            )
            return kio_id

        # Registry has agents: require the target KIO to be alive.
        if not agent_registry.is_alive(kio_id):
            logger.warning(
                "[task_scheduler] {} is stale or unregistered — TASK_NO_CAPABLE_AGENT",
                kio_id,
            )
            return None

        # Capability match: at least one supported_tasks entry must have
        # task_type equal to the kio_id (loose match — works with default descriptors).
        agent_info = registered.get(kio_id, {})
        supported = agent_info.get("supported_tasks", [])
        capable = not supported or any(t.get("task_type") == kio_id for t in supported)
        if not capable:
            logger.warning(
                "[task_scheduler] {} alive but no supported_task matches '{}' — TASK_NO_CAPABLE_AGENT",
                kio_id,
                kio_id,
            )
            return None

        logger.debug("[task_scheduler] step {}: dispatching {}", current_step, kio_id)
        return kio_id

    def find_capable_agent(
        self,
        task_type: str,
        agent_registry: "AgentRegistry",
    ) -> str | None:
        """Find any alive agent that declares support for ``task_type``.

        Used when a KIO has failed and we want to reroute to an equivalent
        capable agent (future: multi-instance KIO pools).
        """
        for agent in agent_registry.list_agents():
            if not agent.get("alive"):
                continue
            for task in agent.get("supported_tasks", []):
                if task.get("task_type") == task_type:
                    logger.debug(
                        "[task_scheduler] found alternate agent {} for {}",
                        agent["kio_id"],
                        task_type,
                    )
                    return agent["kio_id"]
        return None


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_scheduler: TaskScheduler | None = None


def get_task_scheduler() -> TaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler
