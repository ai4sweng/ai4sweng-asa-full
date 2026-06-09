"""Compile the KIO workflow as a LangGraph StateGraph.

Graph topology
--------------
START → plan → run_kio ──[should_hitl]──► hitl → advance ──[should_continue]──► run_kio
                                      └──────────────────────► advance          └──────► complete → END

Key properties:
• PostgreSQL checkpointer (AsyncPostgresSaver) — graph state survives process restarts.
  Falls back to MemorySaver when PostgreSQL is unavailable.
• interrupt() in hitl_node suspends execution; resumed via Command(resume=feedback).
• All service clients are captured as closures so the graph is fully self-contained.
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .event_bus import EventBus
from .graph_nodes import make_nodes
from .graph_state import WorkflowGraphState


def build_workflow_graph(
    sm,
    kio,
    lm,
    bus: EventBus,
    active: dict,
    checkpointer: Any = None,
):
    """Build and compile the workflow graph with all service clients injected.

    Parameters
    ----------
    sm:           SessionClient
    kio:          KioClient
    lm:           LmEngineClient
    bus:          EventBus (SSE)
    active:       shared mutable dict ``session_id → runtime state`` (written by nodes)
    checkpointer: LangGraph checkpointer instance (AsyncPostgresSaver or MemorySaver).
                  If None, falls back to MemorySaver (no persistence across restarts).

    Returns
    -------
    CompiledStateGraph  — ready for ainvoke / astream calls.
    """
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    nodes = make_nodes(sm, kio, lm, bus, active)

    g: StateGraph = StateGraph(WorkflowGraphState)

    # Register nodes
    g.add_node("plan", nodes["plan"])
    g.add_node("run_kio", nodes["run_kio"])
    g.add_node("hitl", nodes["hitl"])
    g.add_node("advance", nodes["advance"])
    g.add_node("complete", nodes["complete"])

    # Edges
    g.add_edge(START, "plan")
    g.add_edge("plan", "run_kio")
    g.add_conditional_edges(
        "run_kio",
        nodes["should_hitl"],
        {"hitl": "hitl", "advance": "advance"},
    )
    g.add_edge("hitl", "advance")
    g.add_conditional_edges(
        "advance",
        nodes["should_continue"],
        {"run_kio": "run_kio", "complete": "complete"},
    )
    g.add_edge("complete", END)

    return g.compile(checkpointer=checkpointer)
