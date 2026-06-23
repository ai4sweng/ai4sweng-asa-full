"""Deterministic, orchestrator-side capability bidding.

Each KIO announces its capabilities over NATS (``CAPABILITY_ANNOUNCEMENT`` →
``AgentRegistry``).  Rather than round-tripping a "can you help?" RPC to every
agent (expensive, and pointless when the bid would be derived from static
capability text anyway), the orchestrator scores each *online* agent's advertised
``supported_tasks`` against the task description and hands the planner a ranked
shortlist.  New partner KIOs become routable the moment they announce — no
planner-prompt edits required.

This is the cheap, deterministic first increment of capability bidding; a true
agent-side bid RPC (where an agent inspects the task and self-nominates) is a
later step and only pays off for agents that do task-specific reasoning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

# Tokens too common to carry routing signal.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with",
        "this", "that", "these", "those", "is", "are", "be", "it", "its", "as",
        "by", "at", "from", "into", "please", "want", "need", "make", "run",
        "code", "file", "files", "agent", "task", "using", "use",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DEFAULT_TOP_K = 5


@dataclass(frozen=True)
class Bid:
    """One agent's fit for the task, scored from its advertised capabilities."""

    kio_id: str
    score: float  # 0..1 relevance
    why: str  # the matched keywords


def _tokenize(text: str) -> set[str]:
    return {
        t for t in _TOKEN_RE.findall((text or "").lower())
        if len(t) >= 3 and t not in _STOPWORDS
    }


def rank_bids(description: str, *, top_k: int = _DEFAULT_TOP_K) -> list[Bid]:
    """Score online agents against ``description``; return the top matches.

    Pure/deterministic and fail-safe: returns ``[]`` when the registry is
    unavailable (e.g. HTTP/dev mode without NATS) or nothing matches.
    """
    task_tokens = _tokenize(description)
    if not task_tokens:
        return []

    try:
        from ..engine.agent_registry import get_agent_registry

        agents = [a for a in get_agent_registry().list_agents() if a.get("alive")]
    except Exception as exc:
        logger.debug("capability bidding skipped (registry unavailable: {})", exc)
        return []

    bids: list[Bid] = []
    for agent in agents:
        kio_id = agent.get("kio_id", "")
        if not kio_id:
            continue
        cap_text = " ".join(
            str(t.get("description", ""))
            for t in (agent.get("supported_tasks") or [])
            if isinstance(t, dict)
        )
        cap_tokens = _tokenize(cap_text)
        if not cap_tokens:
            continue
        matched = task_tokens & cap_tokens
        if not matched:
            continue
        # Normalise by the task's own vocabulary so scores are comparable 0..1.
        score = round(len(matched) / len(task_tokens), 3)
        why = ", ".join(sorted(matched)[:5])
        bids.append(Bid(kio_id=kio_id, score=score, why=why))

    bids.sort(key=lambda b: (-b.score, b.kio_id))
    return bids[:top_k]


def format_bids(bids: list[Bid]) -> str:
    """Render bids as a compact block for the planner prompt ("" when none)."""
    if not bids:
        return ""
    lines = ["Capability bids (online agents ranked by fit to the task):"]
    lines += [f"- {b.kio_id} ({b.score:.2f}): {b.why}" for b in bids]
    return "\n".join(lines)
