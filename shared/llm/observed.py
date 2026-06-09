"""
Observed LLM wrapper — records prompt, output, latency, and tokens to Langfuse/DB.

Thread-safety note: set_request_context() uses contextvars.ContextVar so concurrent
asyncio tasks each see their own (workflow_id, task_id) without stomping each other.
"""

import logging
import time
from contextvars import ContextVar
from typing import Any

from shared.llm.base import BaseLLMProvider, LLMResponse
from shared.llm.bug_detector_llm import (
    BugDetectionChunkResult,
    detect_bugs_from_findings,
)
from shared.llm.repo_file_analyzer import (
    FileAnalysisResult,
    analyze_repository_file,
)
from shared.observability.observability import ObservabilityService
from shared.tools.repo_context_builder import FileContext

logger = logging.getLogger(__name__)

# Per-task context — each asyncio Task gets its own copy via ContextVar.
# Fixes race condition where concurrent LangGraph nodes share one provider instance.
_ctx_workflow_id: ContextVar[str | None] = ContextVar("observed_llm_workflow_id", default=None)
_ctx_task_id: ContextVar[str | None] = ContextVar("observed_llm_task_id", default=None)


class ObservedLLMProvider(BaseLLMProvider):
    """
    Decorator around any BaseLLMProvider that mirrors LLM calls to observability.

    Agents depend only on BaseLLMProvider; request context is set per task in
    BaseAgent before on_task_request runs.

    Concurrency: uses ContextVar so parallel asyncio tasks each carry their own
    (workflow_id, task_id) without interfering with each other.
    """

    def __init__(
        self,
        inner: BaseLLMProvider,
        observability: ObservabilityService | None = None,
    ) -> None:
        self._inner = inner
        self._obs = observability
        # Do NOT store workflow_id/task_id as instance attrs — use ContextVar instead.

    @property
    def provider_name(self) -> str:
        return self._inner.provider_name

    def set_request_context(
        self,
        *,
        workflow_id: str,
        task_id: str | None = None,
    ) -> None:
        """Bind the current asyncio task's (workflow_id, task_id) for attribution.

        Uses ContextVar so parallel LangGraph nodes each see their own context
        without stomping each other's values.
        """
        _ctx_workflow_id.set(workflow_id)
        _ctx_task_id.set(task_id)

    async def complete(self, prompt: str, *, system: str | None = None) -> LLMResponse:
        started = time.monotonic()
        response = await self._inner.complete(prompt, system=system)
        latency_ms = response.latency_ms or (time.monotonic() - started) * 1000

        if not response.prompt:
            response.prompt = prompt
        if response.system is None:
            response.system = system

        # Mirrors every LLM call to PostgreSQL metrics and Langfuse generation spans.
        wf_id = _ctx_workflow_id.get()
        t_id = _ctx_task_id.get()
        if self._obs and wf_id:
            await self._obs.record_llm_generation(
                workflow_id=wf_id,
                prompt=response.prompt,
                output=response.content,
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                latency_ms=latency_ms,
                system=response.system,
                task_id=t_id,
            )

        return response

    async def analyze_repository_file(
        self,
        *,
        issue: str,
        file_ctx: FileContext,
        repo_path: str | None = None,
    ) -> FileAnalysisResult:
        """Per-file repo analysis with Langfuse metadata for parsed findings."""
        result = await analyze_repository_file(
            self,
            issue=issue,
            file_ctx=file_ctx,
            repo_path=repo_path,
        )

        wf_id = _ctx_workflow_id.get()
        if self._obs and wf_id:
            span = self._obs.active_span
            if span and span.langfuse_span:
                try:
                    span.langfuse_span.create_event(
                        name="repo_file_analysis",
                        metadata={
                            "file_path": result.file_path,
                            "prompt_hash": result.prompt_hash,
                            "model": result.raw_response.model,
                            "provider": self.provider_name,
                            "tokens_in": result.raw_response.tokens_in,
                            "tokens_out": result.raw_response.tokens_out,
                            "latency_ms": round(result.latency_ms, 2),
                            "finding_count": len(result.findings),
                            "parse_error": result.parse_error,
                            "detection_source": result.detection_source,
                            "retry_used": result.retry_used,
                            "parse_repaired": result.parse_repaired,
                            "regex_fallback_used": result.regex_fallback_used,
                        },
                        input={
                            "prompt_preview": result.prompt[:2000],
                            "system": "repo_file_analysis",
                        },
                        output={
                            "raw_preview": result.raw_response.content[:2000],
                            "parsed_findings": [
                                f.model_dump() for f in result.findings
                            ],
                        },
                    )
                except Exception:
                    logger.exception(
                        "Langfuse repo_file_analysis event failed for %s",
                        result.file_path,
                    )

        return result

    async def detect_bugs_from_findings(
        self,
        *,
        issue: str,
        findings: list[dict],
        pytest_summary: dict,
        chunk_size: int = 4,
    ) -> list[BugDetectionChunkResult]:
        """Chunked bug validation with Langfuse metadata per chunk."""
        results = await detect_bugs_from_findings(
            self,
            issue=issue,
            findings=findings,
            pytest_summary=pytest_summary,
            chunk_size=chunk_size,
        )

        wf_id = _ctx_workflow_id.get()
        if self._obs and wf_id:
            span = self._obs.active_span
            if span and span.langfuse_span:
                for result in results:
                    try:
                        span.langfuse_span.create_event(
                            name="bug_detection_chunk",
                            metadata={
                                "chunk_index": result.chunk_index,
                                "prompt_hash": result.prompt_hash,
                                "model": result.raw_response.model,
                                "provider": self.provider_name,
                                "bug_count": len(result.bugs),
                                "parse_error": result.parse_error,
                            },
                            input={"prompt_preview": result.prompt[:2000]},
                            output={
                                "raw_preview": result.raw_response.content[:2000],
                                "parsed_bugs": [b.model_dump() for b in result.bugs],
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Langfuse bug_detection_chunk event failed chunk=%s",
                            result.chunk_index,
                        )

        return results

    async def health_check(self) -> None:
        """Forward health checks to providers that support them."""
        checker = getattr(self._inner, "health_check", None)
        if callable(checker):
            await checker()
