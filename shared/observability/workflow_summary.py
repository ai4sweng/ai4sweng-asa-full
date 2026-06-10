"""
Workflow execution summary — latency, tokens, memory, and energy per step.
"""

from __future__ import annotations

from typing import Any

from shared.persistence.session_provider import SessionProvider

_METRIC_KEYS = {
    "tokens_in": ("tokens_in", "tokens"),
    "tokens_out": ("tokens_out", "tokens"),
    "llm_latency_ms": ("llm_latency_ms", "ms"),
    "agent_step_latency_ms": ("agent_step_latency_ms", "ms"),
    "memory_rss_mb_peak": ("memory_rss_mb_peak", "MB"),
    "energy_estimate_mj": ("energy_estimate_mj", "mJ"),
    "human_approval_latency_ms": ("human_approval_latency_ms", "ms"),
}


async def build_execution_summary(
    session_provider: SessionProvider,
    workflow_id: str,
    *,
    replay_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate per-task metrics into a structured execution summary."""
    async with session_provider.read_scope() as repo:
        workflow = await repo.get_workflow(workflow_id)
        tasks = await repo.list_tasks_for_workflow(workflow_id)
        metrics = await repo.list_metrics_for_workflow(workflow_id)

    if not workflow:
        return {"error": f"Workflow {workflow_id} not found"}

    by_task: dict[str | None, dict[str, float]] = {}
    for m in metrics:
        bucket = by_task.setdefault(m.task_id, {})
        bucket[m.metric_name] = m.metric_value

    workflow_metrics = by_task.pop(None, {})
    total_latency_ms = workflow_metrics.get("total_workflow_latency_ms")
    if total_latency_ms is None and workflow.started_at and workflow.completed_at:
        total_latency_ms = (workflow.completed_at - workflow.started_at).total_seconds() * 1000
    if replay_report and replay_report.get("workflow", {}).get("total_workflow_latency_ms"):
        total_latency_ms = replay_report["workflow"]["total_workflow_latency_ms"]

    steps: list[dict[str, Any]] = []
    total_tokens_in = 0.0
    total_tokens_out = 0.0
    total_llm_ms = 0.0
    total_energy_mj = 0.0
    total_memory_peak = 0.0

    ordered_tasks = sorted(
        tasks,
        key=lambda t: (t.started_at or t.created_at, t.step_id),
    )

    for task in ordered_tasks:
        tm = by_task.get(task.id, {})
        tokens_in = int(tm.get("tokens_in", 0))
        tokens_out = int(tm.get("tokens_out", 0))
        llm_ms = tm.get("llm_latency_ms", 0.0)
        step_ms = tm.get("agent_step_latency_ms")
        if step_ms is None and task.started_at and task.completed_at:
            step_ms = (task.completed_at - task.started_at).total_seconds() * 1000
        memory_peak = tm.get("memory_rss_mb_peak", 0.0)
        energy_mj = tm.get("energy_estimate_mj", 0.0)
        approval_ms = tm.get("human_approval_latency_ms")
        approval_energy = tm.get("human_approval_energy_mj", 0.0)
        approval_memory = tm.get("human_approval_memory_mb", 0.0)

        total_tokens_in += tokens_in
        total_tokens_out += tokens_out
        total_llm_ms += llm_ms
        total_energy_mj += energy_mj + approval_energy
        total_memory_peak = max(total_memory_peak, memory_peak, approval_memory)

        step_entry: dict[str, Any] = {
            "step_id": task.step_id,
            "agent": task.agent_role,
            "task_id": task.id,
            "state": task.state,
            "duration_ms": round(step_ms or 0.0, 2),
            "duration_sec": round((step_ms or 0.0) / 1000.0, 3),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "llm_latency_ms": round(llm_ms, 2),
            "memory_rss_mb_peak": round(memory_peak, 2),
            "energy_estimate_mj": round(energy_mj, 2),
        }
        if approval_ms:
            step_entry["human_approval_wait_ms"] = round(approval_ms, 2)
            step_entry["human_approval_energy_mj"] = round(approval_energy, 2)
            step_entry["human_approval_memory_mb"] = round(approval_memory, 2)

        steps.append(step_entry)

    return {
        "workflow_id": workflow_id,
        "workflow_name": workflow.name,
        "workflow_type": (workflow.metadata_ or {}).get("workflow_type", "fibonacci"),
        "state": workflow.state,
        "totals": {
            "latency_ms": round(total_latency_ms or 0.0, 2),
            "latency_sec": round((total_latency_ms or 0.0) / 1000.0, 3),
            "tokens_in": int(total_tokens_in),
            "tokens_out": int(total_tokens_out),
            "tokens_total": int(total_tokens_in + total_tokens_out),
            "llm_latency_ms": round(total_llm_ms, 2),
            "energy_estimate_mj": round(total_energy_mj, 2),
            "memory_rss_mb_peak": round(total_memory_peak, 2),
        },
        "steps": steps,
        "energy_note": (
            "Energy values are demonstration estimates (CPU wall-time + token heuristic), "
            "not measured hardware power draw."
        ),
    }


def print_execution_summary(summary: dict[str, Any], *, workflow_label: str) -> None:
    """Pretty-print execution summary to stdout."""
    if "error" in summary:
        print(summary["error"])
        return

    totals = summary["totals"]
    print("\n" + "=" * 72)
    print(f"EXECUTION SUMMARY — {workflow_label}")
    print(f"Workflow: {summary['workflow_id']}  |  State: {summary['state']}")
    print("=" * 72)
    print("\nTotals:")
    print(f"  Latency:      {totals['latency_ms']:.2f} ms  ({totals['latency_sec']:.3f} s)")
    print(
        f"  Tokens:       in={totals['tokens_in']}  out={totals['tokens_out']}  "
        f"total={totals['tokens_total']}"
    )
    print(f"  LLM time:     {totals['llm_latency_ms']:.2f} ms")
    print(f"  Memory peak:  {totals['memory_rss_mb_peak']:.2f} MB (process RSS)")
    print(f"  Energy (est): {totals['energy_estimate_mj']:.2f} mJ")
    print(f"  Note: {summary.get('energy_note', '')}")

    print("\nPer-step breakdown:")
    print(
        f"  {'Step':<22} {'Agent':<22} {'Time(s)':>8} {'Tok(in)':>8} {'Tok(out)':>9} "
        f"{'LLM(ms)':>9} {'Mem(MB)':>9} {'Energy(mJ)':>12}"
    )
    print("  " + "-" * 68)
    for step in summary["steps"]:
        approval = ""
        if step.get("human_approval_wait_ms"):
            approval = f"  [approval wait {step['human_approval_wait_ms']:.0f} ms]"
        print(
            f"  {step['step_id']:<22} {step['agent']:<22} "
            f"{step['duration_sec']:>8.3f} {step['tokens_in']:>8} {step['tokens_out']:>9} "
            f"{step['llm_latency_ms']:>9.1f} {step['memory_rss_mb_peak']:>9.1f} "
            f"{step['energy_estimate_mj']:>12.1f}{approval}"
        )
    print("=" * 72)


async def build_and_print_summary(
    session_provider: SessionProvider,
    workflow_id: str,
    *,
    workflow_label: str,
    replay_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build summary, persist workflow totals, and print."""
    summary = await build_execution_summary(
        session_provider, workflow_id, replay_report=replay_report
    )
    if "error" not in summary:
        totals = summary["totals"]
        async with session_provider.session_scope() as repo:
            await repo.record_metric_once(
                workflow_id=workflow_id,
                metric_name="total_tokens_in",
                metric_value=float(totals["tokens_in"]),
                unit="tokens",
            )
            await repo.record_metric_once(
                workflow_id=workflow_id,
                metric_name="total_tokens_out",
                metric_value=float(totals["tokens_out"]),
                unit="tokens",
            )
            await repo.record_metric_once(
                workflow_id=workflow_id,
                metric_name="total_energy_estimate_mj",
                metric_value=totals["energy_estimate_mj"],
                unit="mJ",
            )
    print_execution_summary(summary, workflow_label=workflow_label)
    return summary
