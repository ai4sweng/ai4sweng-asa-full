"""
Process resource sampling for per-agent step metrics.

Memory uses psutil RSS when available. Energy is a **demonstration estimate**
based on wall time and token count — not hardware power metering.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore

# Demo energy model (millijoules) — adjustable for coursework reporting.
_CPU_POWER_WATTS = 8.0
_LLM_MJ_PER_TOKEN = 0.08


@dataclass
class ResourceSnapshot:
    """Point-in-time process resource sample."""

    rss_mb: float
    timestamp: float


def sample_process() -> ResourceSnapshot:
    """Sample current process resident memory."""
    rss_mb = 0.0
    if psutil is not None:
        rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
    return ResourceSnapshot(rss_mb=round(rss_mb, 2), timestamp=time.monotonic())


def estimate_energy_mj(
    *,
    duration_ms: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> float:
    """
    Rough energy estimate for demo reporting.

    cpu_mj = wall_time(s) × assumed_cpu_watts × 1000
    llm_mj = (tokens_in + tokens_out) × per_token_factor
    """
    duration_sec = max(0.0, duration_ms / 1000.0)
    cpu_mj = duration_sec * _CPU_POWER_WATTS * 1000.0
    token_mj = (tokens_in + tokens_out) * _LLM_MJ_PER_TOKEN
    return round(cpu_mj + token_mj, 2)
