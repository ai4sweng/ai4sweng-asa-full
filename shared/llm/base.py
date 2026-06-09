"""
Base LLM provider interface.

All agents depend only on BaseLLMProvider so providers can be swapped
without changing agent code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """Structured LLM response with token metrics for observability."""

    content: str
    tokens_in: int
    tokens_out: int
    model: str = "mock"
    latency_ms: float = 0.0
    prompt: str = ""
    system: str | None = None


class BaseLLMProvider(ABC):
    """Abstract LLM provider contract."""

    @abstractmethod
    async def complete(self, prompt: str, *, system: str | None = None) -> LLMResponse:
        """Generate a completion for the given prompt."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier."""

    async def analyze_repository_file(
        self,
        *,
        issue: str,
        file_ctx: "FileContext",
        repo_path: str | None = None,
    ) -> "FileAnalysisResult":
        """Analyze one repository file and return structured findings."""
        from shared.llm.repo_file_analyzer import analyze_repository_file

        return await analyze_repository_file(
            self,
            issue=issue,
            file_ctx=file_ctx,
            repo_path=repo_path,
        )

    async def detect_bugs_from_findings(
        self,
        *,
        issue: str,
        findings: list[dict],
        pytest_summary: dict,
        chunk_size: int = 4,
    ) -> list:
        """Validate repo findings in sequential LLM chunks."""
        from shared.llm.bug_detector_llm import detect_bugs_from_findings

        return await detect_bugs_from_findings(
            self,
            issue=issue,
            findings=findings,
            pytest_summary=pytest_summary,
            chunk_size=chunk_size,
        )
