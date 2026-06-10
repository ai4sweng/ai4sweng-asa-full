"""shared.a2a — Agent-to-Agent direct invocation.

KIOs can call each other directly without routing through the orchestrator's
workflow loop.  The transport mirrors the orchestrator's KioClient: NATS
JetStream primary, HTTP fallback.

Typical use-case: kio3 (repo scanner) completing its analysis and directly
dispatching findings to kio5 (bug detector) as a sub-task, then returning
a combined artifact to the orchestrator.
"""

from shared.a2a.client import A2AClient, get_a2a_client

__all__ = ["A2AClient", "get_a2a_client"]
