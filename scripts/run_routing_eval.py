#!/usr/bin/env python
"""Run the routing eval against the live LM Engine and print a report.

Usage:
    python scripts/run_routing_eval.py
    python scripts/run_routing_eval.py --no-reflection   # score raw planner output

Requires the LM Engine to be reachable (LM_ENGINE_URL / LM_ENGINE_HOST:PORT).
Exit code is non-zero when first-step accuracy falls below --min-first-step, so
this doubles as a regression gate in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as a standalone script: put the repo root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.orchestrator.src.eval.routing_eval import (  # noqa: E402
    DEFAULT_DATASET,
    evaluate,
)
from apps.orchestrator.src.services.lm_client import get_lm_client  # noqa: E402


async def _main(apply_reflection: bool, min_first_step: float) -> int:
    client = get_lm_client()

    async def router(description: str) -> tuple[list[str], bool]:
        kios, _reasoning, used_fallback = await client.plan_workflow(description, "eval")
        return kios, used_fallback

    try:
        report = await evaluate(router, DEFAULT_DATASET, apply_reflection=apply_reflection)
    finally:
        await client.close()

    print(report.summary())
    if report.first_step_acc < min_first_step:
        print(
            f"\nFAIL: first_step_acc {report.first_step_acc:.1%} "
            f"< threshold {min_first_step:.1%}",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Routing eval against the live LM Engine")
    parser.add_argument(
        "--no-reflection",
        action="store_true",
        help="score the planner's raw output without process reflection",
    )
    parser.add_argument(
        "--min-first-step",
        type=float,
        default=0.0,
        help="fail (exit 1) if first-step accuracy is below this fraction",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(_main(not args.no_reflection, args.min_first_step))
    )


if __name__ == "__main__":
    main()
