"""Dynamic few-shot retrieval for the routing planner.

Instead of a fixed example block hardcoded in the planner prompt, the planner
retrieves the few exemplars most similar to the *current* task description and
injects those.  This keeps the small model's in-context guidance relevant per
request, and lets the example bank grow (with partner- or real-traffic-derived
exemplars) without editing the prompt — the scaling problem hardcoded few-shot
has in a partner-federated platform.

Similarity is lexical (token Jaccard): deterministic, dependency-free, and good
enough to surface the closest routes.  It can be swapped for embeddings later
behind the same ``retrieve()`` interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "to", "of", "in", "this", "that", "for", "and", "or",
        "is", "it", "me", "my", "i", "with", "on", "please", "can", "you", "do",
    }
)


@dataclass(frozen=True)
class Exemplar:
    description: str
    kio_sequence: tuple[str, ...]
    reasoning: str


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS}


def _similarity(a: set[str], b: set[str]) -> float:
    """Jaccard overlap of two token sets (0.0 – 1.0)."""
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


# Seed bank.  Extend with partner-supplied or real-traffic exemplars over time.
DEFAULT_EXEMPLARS: tuple[Exemplar, ...] = (
    Exemplar(
        "I need an application to find the protein from a list of foods",
        ("kio9",),
        "build request, no code → code generation",
    ),
    Exemplar("build me a tool that converts CSV to JSON", ("kio9",), "code generation"),
    Exemplar(
        "write a python function that reverses a string", ("kio9",), "code generation"
    ),
    Exemplar(
        "find security bugs in this repository", ("kio3", "kio5"), "analyze existing repo"
    ),
    Exemplar(
        "review the existing codebase and tell me what is wrong",
        ("kio3", "kio5"),
        "analyze existing repo",
    ),
    Exemplar(
        "check this code snippet for bugs", ("kio5",), "bug detect on provided code"
    ),
    Exemplar(
        "fix the bugs in this repo and re-run the tests",
        ("kio3", "kio5", "kio6", "kio7"),
        "full fix loop on a repo",
    ),
    Exemplar(
        "build a calculator app and check it for bugs",
        ("kio9", "kio5"),
        "generate then bug-check",
    ),
)


def retrieve(
    description: str,
    k: int = 3,
    exemplars: tuple[Exemplar, ...] = DEFAULT_EXEMPLARS,
) -> list[Exemplar]:
    """Return up to ``k`` exemplars most similar to ``description``.

    Exemplars with no token overlap are excluded; if nothing overlaps at all,
    the first ``k`` of the bank are returned so the prompt always has examples.
    """
    q = _tokens(description)
    scored = sorted(
        exemplars, key=lambda e: _similarity(q, _tokens(e.description)), reverse=True
    )
    relevant = [e for e in scored if _similarity(q, _tokens(e.description)) > 0]
    return (relevant or list(exemplars))[:k]


def format_examples(exemplars: list[Exemplar]) -> str:
    """Render exemplars as the planner prompt's ``Examples:`` block."""
    lines = ["Examples:"]
    for e in exemplars:
        seq = ", ".join(f'"{k}"' for k in e.kio_sequence)
        lines.append(
            f'- "{e.description}" → '
            f'{{"kio_sequence": [{seq}], "reasoning": "{e.reasoning}"}}'
        )
    return "\n".join(lines)
