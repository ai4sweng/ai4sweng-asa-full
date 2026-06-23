#!/usr/bin/env python
"""Run the full behavior-spec eval and print one categorized scorecard.

The pure (reflection) layer always runs — it is deterministic and offline. The
live layers (routing / clarify / long-prompt) run against the LM Engine planner
when reachable; if a probe fails (or --pure-only is given) they are SKIPPED, not
failed, so the scorecard stays honest about what was measured.

Usage:
    python scripts/run_eval.py                  # pure + live (if reachable)
    python scripts/run_eval.py --pure-only      # deterministic layer only
    python scripts/run_eval.py --json out.json  # also write machine-readable report
    python scripts/run_eval.py --min-closeness 0.8   # exit 1 below threshold

``--pace`` sleeps between live cases (each makes 2 model calls); raise it under a
low hosted rate limit so clear prompts don't get a fallback substituted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.orchestrator.src.eval.behavior_spec import run_full_eval  # noqa: E402


async def _probe(planner) -> bool:
    """Cheap reachability check: one clarify call. Network failure → not live."""
    try:
        await planner.assess_clarification("ping", "probe")
        return True
    except Exception as exc:  # noqa: BLE001 — any transport error means "not live"
        print(f"[run_eval] live planner unreachable ({exc!r}); running pure layer only")
        return False


async def _main(pure_only: bool, pace: float, json_path: str | None, min_closeness: float) -> int:
    planner = None
    client = None
    if not pure_only:
        from apps.orchestrator.src.services.lm_client import get_lm_client

        client = get_lm_client()
        if not await _probe(client):
            await client.close()
            client = None
        else:
            planner = client

    try:
        scorecard, raw = await run_full_eval(planner, pace_seconds=pace)
    finally:
        if client is not None:
            await client.close()

    print(scorecard.summary())

    # Per-layer failure detail (the reports expose their own summaries).
    for name, report in raw.items():
        summary = getattr(report, "summary", None)
        if callable(summary):
            text = summary()
            if "failures:" in text or "misses:" in text:
                print()
                print(text)

    if json_path:
        Path(json_path).write_text(json.dumps(scorecard.to_dict(), indent=2))
        print(f"\n[run_eval] wrote {json_path}")

    closeness = scorecard.spec_closeness
    if closeness is not None and closeness < min_closeness:
        print(
            f"\nFAIL: spec_closeness {closeness:.1%} < threshold {min_closeness:.1%}",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Full behavior-spec eval scorecard")
    p.add_argument("--pure-only", action="store_true", help="skip live layers (deterministic only)")
    p.add_argument("--pace", type=float, default=0.0, help="seconds to sleep between live cases")
    p.add_argument("--json", dest="json_path", default=None, help="write the scorecard as JSON to this path")
    p.add_argument(
        "--min-closeness",
        type=float,
        default=0.0,
        help="exit 1 if spec_closeness falls below this fraction",
    )
    args = p.parse_args()
    raise SystemExit(
        asyncio.run(_main(args.pure_only, args.pace, args.json_path, args.min_closeness))
    )


if __name__ == "__main__":
    main()
