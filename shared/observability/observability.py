"""
Observability service for workflow tracing and metrics.

Trace hierarchy:
  Workflow
    ├─ PlannerAgent
    ├─ CodeGeneratorAgent
    ├─ DebuggerAgent
    │   └─ PythonRunner
    ├─ HumanApproval
    └─ FixerReporterAgent
"""

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from langfuse import propagate_attributes
from langfuse.types import TraceContext

from shared.observability.langfuse_client import get_langfuse_client, is_langfuse_enabled
from shared.persistence.session_provider import SessionProvider
from shared.tools.resource_monitor import estimate_energy_mj

logger = logging.getLogger(__name__)


@dataclass
class SpanContext:
    """Active span context for nested trace hierarchy."""

    trace_id: str
    span_id: str
    span_name: str
    parent_span_id: str | None = None
    workflow_name: str | None = None
    start_time: float = field(default_factory=time.monotonic)
    langfuse_span: Any = None


class ObservabilityService:
    """Records traces, spans, artifacts, errors, and metrics."""

    def __init__(self, session_provider: SessionProvider | None = None) -> None:
        self._session_provider = session_provider
        self._workflow_trace: SpanContext | None = None
        self._active_span: SpanContext | None = None
        self._langfuse_trace_id: str | None = None

    @property
    def active_span(self) -> SpanContext | None:
        """Currently executing agent span (if any)."""
        return self._active_span

    def start_workflow_trace(
        self,
        workflow_id: str,
        workflow_name: str,
    ) -> SpanContext:
        """Start top-level workflow trace."""
        langfuse = get_langfuse_client()
        trace_id = langfuse.create_trace_id() if langfuse else str(uuid4())
        ctx = SpanContext(
            trace_id=trace_id,
            span_id="",
            span_name="workflow",
            workflow_name=workflow_name,
        )

        if langfuse:
            try:
                ctx.langfuse_span = langfuse.start_observation(
                    name=workflow_name,
                    as_type="span",
                    trace_context=TraceContext(trace_id=trace_id),
                    metadata={"workflow_id": workflow_id},
                )
                ctx.span_id = ctx.langfuse_span.id
            except Exception:
                logger.exception("Langfuse workflow trace failed")

        logger.info(
            "Started workflow trace workflow_id=%s trace_id=%s",
            workflow_id,
            trace_id,
        )
        self._workflow_trace = ctx
        self._langfuse_trace_id = trace_id
        return ctx

    def update_workflow_identity(self, workflow_id: str) -> None:
        """Bind the real workflow_id to the root Langfuse span after creation."""
        ctx = self._workflow_trace
        if ctx and ctx.langfuse_span:
            try:
                ctx.langfuse_span.update(metadata={"workflow_id": workflow_id})
            except Exception:
                logger.exception("Langfuse workflow metadata update failed")

    def _start_langfuse_child(
        self,
        *,
        parent_ctx: SpanContext,
        name: str,
        as_type: str,
        **kwargs: Any,
    ) -> Any | None:
        """
        Start a child observation with explicit trace_id + parent_span_id.

        Async agents do not share OTEL context across tasks; explicit parent
        linkage keeps the full workflow under one fibonacci_demo trace tree.
        """
        langfuse = get_langfuse_client()
        if not langfuse or not parent_ctx.span_id:
            return None
        try:
            return langfuse.start_observation(
                name=name,
                as_type=as_type,
                trace_context=TraceContext(
                    trace_id=parent_ctx.trace_id,
                    parent_span_id=parent_ctx.span_id,
                ),
                **kwargs,
            )
        except Exception:
            logger.exception("Langfuse child observation failed for %s", name)
            return None

    async def _persist_span(
        self,
        *,
        workflow_id: str,
        agent_name: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
    ) -> None:
        """Persist span in an isolated session (awaited, not fire-and-forget)."""
        if not self._session_provider:
            return
        async with self._session_provider.session_scope() as repo:
            await repo.record_span(
                workflow_id=workflow_id,
                span_name=agent_name,
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )

    async def start_agent_span(
        self,
        agent_name: str,
        *,
        parent: SpanContext | None = None,
        workflow_id: str | None = None,
    ) -> SpanContext:
        """Start an agent-level span under the workflow trace."""
        parent_ctx = parent or self._workflow_trace
        trace_id = parent_ctx.trace_id if parent_ctx else str(uuid4())
        span_id = str(uuid4())
        parent_span_id = parent_ctx.span_id if parent_ctx else None

        ctx = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            span_name=agent_name,
            parent_span_id=parent_span_id,
        )

        if parent_ctx:
            ctx.langfuse_span = self._start_langfuse_child(
                parent_ctx=parent_ctx,
                name=agent_name,
                as_type="agent",
            )
            if ctx.langfuse_span:
                span_id = ctx.langfuse_span.id
                ctx.span_id = span_id

        if workflow_id:
            await self._persist_span(
                workflow_id=workflow_id,
                agent_name=agent_name,
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )

        logger.debug("Started agent span %s span_id=%s", agent_name, span_id)
        return ctx

    async def start_nested_span(
        self,
        name: str,
        *,
        parent: SpanContext | None = None,
        workflow_id: str | None = None,
        as_type: str = "tool",
    ) -> SpanContext:
        """Start a child span under the active agent span or workflow root."""
        parent_ctx = parent or self._active_span or self._workflow_trace
        trace_id = parent_ctx.trace_id if parent_ctx else str(uuid4())
        span_id = str(uuid4())
        parent_span_id = parent_ctx.span_id if parent_ctx else None

        ctx = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            span_name=name,
            parent_span_id=parent_span_id,
        )

        if parent_ctx:
            ctx.langfuse_span = self._start_langfuse_child(
                parent_ctx=parent_ctx,
                name=name,
                as_type=as_type,
            )
            if ctx.langfuse_span:
                span_id = ctx.langfuse_span.id
                ctx.span_id = span_id

        if workflow_id:
            await self._persist_span(
                workflow_id=workflow_id,
                agent_name=name,
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )

        logger.debug("Started nested span %s span_id=%s", name, span_id)
        return ctx

    def _trace_name_and_metadata(self, workflow_id: str) -> tuple[str, dict[str, str]]:
        trace_name = (
            self._workflow_trace.workflow_name
            if self._workflow_trace and self._workflow_trace.workflow_name
            else "workflow"
        )
        return trace_name, {"workflow_id": workflow_id}

    @asynccontextmanager
    async def nested_span(
        self,
        name: str,
        workflow_id: str,
        *,
        as_type: str = "tool",
        parent: SpanContext | None = None,
    ):
        """Context manager for tool/step spans nested under the active agent."""
        trace_name, metadata = self._trace_name_and_metadata(workflow_id)
        with propagate_attributes(
            trace_name=trace_name,
            session_id=workflow_id,
            metadata=metadata,
        ):
            span = await self.start_nested_span(
                name,
                parent=parent,
                workflow_id=workflow_id,
                as_type=as_type,
            )
            try:
                yield span
            finally:
                if span.langfuse_span:
                    try:
                        span.langfuse_span.end()
                    except Exception:
                        logger.exception("Langfuse nested span end failed for %s", name)

    @asynccontextmanager
    async def workflow_step_span(self, name: str, workflow_id: str):
        """Context manager for orchestrator steps (e.g. HumanApproval) under workflow root."""
        trace_name, metadata = self._trace_name_and_metadata(workflow_id)
        with propagate_attributes(
            trace_name=trace_name,
            session_id=workflow_id,
            metadata=metadata,
        ):
            span = await self.start_nested_span(
                name,
                parent=self._workflow_trace,
                workflow_id=workflow_id,
                as_type="span",
            )
            try:
                yield span
            finally:
                if span.langfuse_span:
                    try:
                        span.langfuse_span.end()
                    except Exception:
                        logger.exception("Langfuse workflow step end failed for %s", name)

    async def record_artifact_event(
        self,
        *,
        workflow_id: str,
        artifact_type: str,
        artifact_id: str,
        span: SpanContext | None = None,
    ) -> None:
        """Record artifact creation in Langfuse and metrics."""
        if span and span.langfuse_span:
            try:
                span.langfuse_span.create_event(
                    name="artifact_created",
                    metadata={"artifact_type": artifact_type, "artifact_id": artifact_id},
                )
            except Exception:
                logger.exception("Langfuse artifact event failed")

        logger.info(
            "Artifact created workflow_id=%s type=%s id=%s",
            workflow_id,
            artifact_type,
            artifact_id,
        )

    def record_span_io(
        self,
        *,
        span: SpanContext | None = None,
        input: Any | None = None,
        output: Any | None = None,
    ) -> None:
        """Attach Langfuse Preview input/output on the active agent span."""
        span = span or self._active_span
        if not span or not span.langfuse_span:
            return
        updates: dict[str, Any] = {}
        if input is not None:
            updates["input"] = input
        if output is not None:
            updates["output"] = output
        if not updates:
            return
        try:
            span.langfuse_span.update(**updates)
        except Exception:
            logger.exception("Langfuse span I/O update failed")

    def record_error(
        self,
        error: Exception | str,
        *,
        span: SpanContext | None = None,
    ) -> None:
        """Record error on active span."""
        message = str(error)
        if span and span.langfuse_span:
            try:
                span.langfuse_span.create_event(
                    name="error", metadata={"message": message}
                )
            except Exception:
                logger.exception("Langfuse error event failed")
        logger.error("Recorded error: %s", message)

    async def record_llm_generation(
        self,
        *,
        workflow_id: str,
        prompt: str,
        output: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        system: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """
        Persist LLM metrics and create a Langfuse generation with real prompt/output.

        Called by ObservedLLMProvider — agents should not duplicate token metrics.
        """
        if self._session_provider and task_id:
            async with self._session_provider.session_scope() as repo:
                await repo.record_metric_add(
                    workflow_id=workflow_id,
                    metric_name="tokens_in",
                    delta=float(tokens_in),
                    task_id=task_id,
                    unit="tokens",
                )
                await repo.record_metric_add(
                    workflow_id=workflow_id,
                    metric_name="tokens_out",
                    delta=float(tokens_out),
                    task_id=task_id,
                    unit="tokens",
                )
                await repo.record_metric_add(
                    workflow_id=workflow_id,
                    metric_name="llm_latency_ms",
                    delta=latency_ms,
                    task_id=task_id,
                    unit="ms",
                )
        elif self._session_provider:
            await self.record_metrics(
                workflow_id=workflow_id,
                metrics={
                    "tokens_in": float(tokens_in),
                    "tokens_out": float(tokens_out),
                    "llm_latency_ms": latency_ms,
                },
                task_id=task_id,
                model=model,
                record_langfuse_generation=False,
            )

        span = self._active_span
        if not span:
            return

        llm_input: dict[str, Any] = {"prompt": prompt}
        if system:
            llm_input["system"] = system

        # Unique name per agent — avoids Langfuse UI mixing identical "llm" children.
        agent_label = span.span_name or "unknown"
        generation = self._start_langfuse_child(
            parent_ctx=span,
            name=f"llm/{agent_label}",
            as_type="generation",
            model=model,
            input=llm_input,
            output={"content": output},
            usage_details={"input": tokens_in, "output": tokens_out},
            metadata={
                "agent": agent_label,
                "tokens_in": str(tokens_in),
                "tokens_out": str(tokens_out),
                "llm_latency_ms": str(round(latency_ms, 2)),
            },
        )
        if generation:
            try:
                generation.update(input=llm_input, output={"content": output})
            except Exception:
                logger.exception("Langfuse generation output update failed")
            generation.end()

    async def record_agent_step_completion(
        self,
        *,
        workflow_id: str,
        task_id: str,
        duration_ms: float,
        memory_rss_mb_peak: float,
    ) -> None:
        """Record wall-clock, memory peak, and estimated energy for one agent step."""
        tokens_in = 0
        tokens_out = 0
        if self._session_provider:
            async with self._session_provider.read_scope() as repo:
                for m in await repo.list_metrics_for_workflow(workflow_id):
                    if m.task_id != task_id:
                        continue
                    if m.metric_name == "tokens_in":
                        tokens_in = int(m.metric_value)
                    elif m.metric_name == "tokens_out":
                        tokens_out = int(m.metric_value)

        energy_mj = estimate_energy_mj(
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        await self.record_metrics(
            workflow_id=workflow_id,
            task_id=task_id,
            metrics={
                "agent_step_latency_ms": duration_ms,
                "memory_rss_mb_peak": memory_rss_mb_peak,
                "energy_estimate_mj": energy_mj,
            },
            record_langfuse_generation=False,
        )

    @staticmethod
    def _metric_unit(name: str) -> str:
        if "token" in name:
            return "tokens"
        if "memory" in name or name.endswith("_mb") or name.endswith("_mb_peak"):
            return "MB"
        if "energy" in name or name.endswith("_mj"):
            return "mJ"
        return "ms"

    async def record_metrics(
        self,
        *,
        workflow_id: str,
        metrics: dict[str, float],
        task_id: str | None = None,
        span: SpanContext | None = None,
        model: str | None = None,
        record_langfuse_generation: bool = True,
    ) -> None:
        """Persist metrics to PostgreSQL and mirror token/latency data to Langfuse."""
        span = span or self._active_span

        if self._session_provider:
            async with self._session_provider.session_scope() as repo:
                for name, value in metrics.items():
                    unit = self._metric_unit(name)
                    await repo.record_metric_once(
                        workflow_id=workflow_id,
                        metric_name=name,
                        metric_value=value,
                        task_id=task_id,
                        unit=unit,
                    )

        if not span or not span.langfuse_span:
            return

        tokens_in = int(metrics.get("tokens_in", 0))
        tokens_out = int(metrics.get("tokens_out", 0))
        span_metadata = {
            k: str(v)
            for k, v in metrics.items()
            if k not in ("tokens_in", "tokens_out")
        }

        try:
            if span_metadata:
                span.langfuse_span.update(metadata=span_metadata)

            if record_langfuse_generation and (tokens_in or tokens_out):
                generation = self._start_langfuse_child(
                    parent_ctx=span,
                    name="llm",
                    as_type="generation",
                    model=model or "unknown",
                    usage_details={"input": tokens_in, "output": tokens_out},
                    metadata={
                        "tokens_in": str(tokens_in),
                        "tokens_out": str(tokens_out),
                    },
                )
                if generation:
                    generation.end()
        except Exception:
            logger.exception("Langfuse metrics update failed")

    def end_workflow_trace(self, *, workflow_id: str, status: str = "COMPLETED") -> None:
        """Close the root workflow span and finalize the Langfuse trace."""
        ctx = self._workflow_trace
        if ctx and ctx.langfuse_span:
            try:
                ctx.langfuse_span.update(
                    output={"status": status, "workflow_id": workflow_id},
                )
                ctx.langfuse_span.end()
            except Exception:
                logger.exception("Langfuse workflow span end failed")
        self._workflow_trace = None

    def get_trace_url(self) -> str | None:
        """Return Langfuse UI URL for the active workflow trace."""
        trace_id = self._langfuse_trace_id or (
            self._workflow_trace.trace_id if self._workflow_trace else None
        )
        if not trace_id:
            return None
        client = get_langfuse_client()
        if not client:
            return None
        try:
            return client.get_trace_url(trace_id=trace_id)
        except Exception:
            logger.exception("Langfuse get_trace_url failed")
            return None

    @asynccontextmanager
    async def agent_span(
        self,
        agent_name: str,
        workflow_id: str,
        task_id: str | None = None,
    ):
        """Context manager that opens and closes an agent span."""
        trace_name, metadata = self._trace_name_and_metadata(workflow_id)
        with propagate_attributes(
            trace_name=trace_name,
            session_id=workflow_id,
            metadata=metadata,
        ):
            span = await self.start_agent_span(agent_name, workflow_id=workflow_id)
            prev_active = self._active_span
            self._active_span = span
            try:
                yield span
            finally:
                self._active_span = prev_active
                elapsed_ms = (time.monotonic() - span.start_time) * 1000
                await self.record_metrics(
                    workflow_id=workflow_id,
                    metrics={"latency_ms": elapsed_ms},
                    task_id=task_id,
                    span=span,
                    record_langfuse_generation=False,
                )
                if span.langfuse_span:
                    try:
                        span.langfuse_span.end()
                    except Exception:
                        logger.exception("Langfuse span end failed")

    def flush(self) -> None:
        """Flush pending Langfuse events."""
        if is_langfuse_enabled():
            client = get_langfuse_client()
            if client:
                try:
                    client.flush()
                except Exception:
                    logger.exception("Langfuse flush failed")
