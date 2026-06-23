#!/usr/bin/env python
"""Run the plan-stage eval against the live planner (Haiku) and print a report.

Behavioral checks for large / ambiguous prompts: clarification gate fires when it
should, required agents are routed, and the report (kio8) is never dropped.

Usage:
    python scripts/run_plan_stage_eval.py
    python scripts/run_plan_stage_eval.py --min-pass 0.75   # CI regression gate

Makes live planner calls (Claude Haiku when PLANNER_PROVIDER=anthropic, else the
local LM Engine). Exit code is non-zero when pass_rate < --min-pass.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.orchestrator.src.eval.plan_stage_eval import (  # noqa: E402
    DEFAULT_DATASET,
    evaluate_plan_stage,
)
from apps.orchestrator.src.services.lm_client import get_lm_client  # noqa: E402


async def _main(min_pass: float, pace: float) -> int:
    client = get_lm_client()
    try:
        report = await evaluate_plan_stage(client, DEFAULT_DATASET, pace_seconds=pace)
    finally:
        await client.close()

    print(report.summary())
    if report.pass_rate < min_pass:
        print(
            f"\nFAIL: pass_rate {report.pass_rate:.1%} < threshold {min_pass:.1%}",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan-stage eval against the live planner")
    parser.add_argument(
        "--min-pass",
        type=float,
        default=0.0,
        help="fail (exit 1) if the pass rate is below this fraction",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=25.0,
        help="seconds to sleep between cases (each case = 2 model calls; "
        "Haiku's 5 RPM needs ~24s). Set 0 to disable.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.min_pass, args.pace)))


if __name__ == "__main__":
    main()
