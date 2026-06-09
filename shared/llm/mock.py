"""
Mock LLM provider for MVP demonstration.

Returns deterministic canned responses — no real LLM calls required.

Keyword routing uses whole-word matching where needed so substrings like
"pagination" do not false-match "plan".
"""

import json
import re

from shared.constants import MOCK_TOKENS_IN, MOCK_TOKENS_OUT
from shared.llm.base import BaseLLMProvider, LLMResponse


def _has_word(text: str, word: str) -> bool:
    """True when word appears as a whole token (not a substring of another word)."""
    return re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE) is not None


class MockLLMProvider(BaseLLMProvider):
    """Deterministic mock responses keyed by prompt and system context."""

    @property
    def provider_name(self) -> str:
        return "mock"

    async def complete(self, prompt: str, *, system: str | None = None) -> LLMResponse:
        """Return canned response based on prompt context."""
        prompt_lower = prompt.lower()
        system_lower = (system or "").lower()
        combined = f"{prompt_lower} {system_lower}"

        # --- Repo audit workflow (checked before Fibonacci keyword overlap) ---
        if (
            "repo" in combined
            or "repository" in combined
            or _has_word(combined, "audit")
        ):
            content = self._repo_audit_response(prompt_lower, system_lower)
        elif self._is_fibonacci_planner(prompt_lower):
            content = (
                "Plan: Generate a Python function fibonacci_up_to(n) that returns "
                "all Fibonacci numbers up to n inclusive."
            )
        elif self._is_fibonacci_code_gen(prompt_lower):
            content = "Generated faulty fibonacci implementation with off-by-one bug."
        elif self._is_fibonacci_debug(prompt_lower):
            content = (
                "Root cause: off-by-one error in range boundary causes IndexError "
                "when accessing fib sequence."
            )
        elif self._is_fibonacci_fix_report(prompt_lower):
            content = "Fixed code and final report generated successfully."
        else:
            content = f"Mock response for: {prompt[:80]}"

        return LLMResponse(
            content=content,
            tokens_in=MOCK_TOKENS_IN,
            tokens_out=MOCK_TOKENS_OUT,
            model="mock-v1",
        )

    @staticmethod
    def _repo_audit_response(prompt_lower: str, system_lower: str) -> str:
        if (
            "findings to validate" in prompt_lower
            and (
                "bug detection agent" in system_lower
                or "repair invalid" in system_lower
            )
        ):
            return MockLLMProvider._mock_bug_detection_json(prompt_lower)
        if "auditing a python repository" in system_lower and "audit issue:" in prompt_lower:
            return MockLLMProvider._mock_file_analysis_json(prompt_lower)
        if "summarize" in prompt_lower and "analysis" in prompt_lower:
            return (
                "Repository analysis: auth, users, and inventory modules scanned. "
                "Static findings include SQL injection in user search, off-by-one "
                "pagination, missing profile null-guard, credential logging, and N+1 queries."
            )
        if "pytest" in prompt_lower or _has_word(prompt_lower, "test"):
            return (
                "Proposed pytest cases: reset_password admin-only guard, "
                "delete_product endpoint coverage, low-stock pagination edge cases."
            )
        if "bugs found" in prompt_lower or _has_word(prompt_lower, "bugs"):
            return (
                "Bug catalog: SQL injection (search_users), null profile access, "
                "pagination off-by-one, JWT/password logging at INFO, N+1 category queries."
            )
        if _has_word(prompt_lower, "patch") or "patches" in prompt_lower:
            return (
                "Dry-run patch proposal: parameterized SQL, profile None-guard, "
                "1-based pagination offset, redact secrets from logs, batch category fetch."
            )
        if "rerun" in prompt_lower or "pytest rerun" in prompt_lower:
            return (
                "Pytest rerun complete (MVP dry-run: patches not written to disk). "
                "Existing test suite results recorded for evidence."
            )
        if "evidence" in prompt_lower or "evidence report" in prompt_lower:
            return (
                "Evidence report compiled: ISSUE → REPO_ANALYSIS → GENERATED_TESTS → "
                "BUG_REPORT → PATCH → TEST_RERUN → EVIDENCE_REPORT with human approval."
            )
        if "repository analyst" in system_lower:
            return (
                "Repo audit LLM summary: security and correctness review of FastAPI "
                "inventory API completed."
            )
        return f"Repo audit mock response for: {prompt_lower[:100]}"

    @staticmethod
    def _mock_file_analysis_json(prompt_lower: str) -> str:
        """Structured JSON for per-file repo analysis (tests and mock runs)."""
        findings: list[dict[str, str]] = []
        if "app/users/service.py" in prompt_lower:
            findings.extend(
                [
                    {
                        "bug_id": "MOCK-SQL-001",
                        "file_path": "app/users/service.py",
                        "line_hint": "59",
                        "severity": "HIGH",
                        "category": "SECURITY",
                        "description": "SQL string interpolation in search_users_by_name",
                        "evidence": "f\"SELECT ... LIKE '%{",
                        "recommended_fix": "Use parameterized query with :q bind",
                    },
                    {
                        "bug_id": "MOCK-NULL-001",
                        "file_path": "app/users/service.py",
                        "line_hint": "78",
                        "severity": "HIGH",
                        "category": "BUG",
                        "description": "profile accessed without None guard",
                        "evidence": ".profile.",
                        "recommended_fix": "Return defaults when profile is None",
                    },
                ]
            )
        if "app/inventory/service.py" in prompt_lower:
            findings.extend(
                [
                    {
                        "bug_id": "MOCK-PAGE-001",
                        "file_path": "app/inventory/service.py",
                        "line_hint": "126",
                        "severity": "MEDIUM",
                        "category": "BUG",
                        "description": "Pagination loop starts at index 1",
                        "evidence": "range(1, len(",
                        "recommended_fix": "Use range(0, len(...)) or correct offset",
                    },
                    {
                        "bug_id": "MOCK-N1-001",
                        "file_path": "app/inventory/service.py",
                        "line_hint": "100",
                        "severity": "MEDIUM",
                        "category": "PERFORMANCE",
                        "description": "N+1 queries in product enrichment",
                        "evidence": "for product in products",
                        "recommended_fix": "Batch-fetch categories in one query",
                    },
                ]
            )
        if "app/auth/service.py" in prompt_lower:
            findings.append(
                {
                    "bug_id": "MOCK-LOG-001",
                    "file_path": "app/auth/service.py",
                    "line_hint": "21",
                    "severity": "HIGH",
                    "category": "SECURITY",
                    "description": "Password logged at INFO level",
                    "evidence": "password=%s",
                    "recommended_fix": "Remove credential values from logs",
                }
            )
        if "tests/test_auth.py" in prompt_lower:
            findings.append(
                {
                    "bug_id": "MOCK-TEST-001",
                    "file_path": "tests/test_auth.py",
                    "line_hint": "0",
                    "severity": "LOW",
                    "category": "TEST",
                    "description": "No test for reset-password endpoint",
                    "evidence": "missing reset-password coverage",
                    "recommended_fix": "Add admin-only reset-password pytest",
                }
            )
        return json.dumps({"findings": findings})

    @staticmethod
    def _mock_bug_detection_json(prompt_lower: str) -> str:
        """Structured JSON for chunked bug validation (mock runs)."""
        bugs: list[dict[str, str | int]] = []
        if "app/users/service.py" in prompt_lower:
            bugs.append(
                {
                    "id": "MOCK-BUG-SQL",
                    "kind": "sql_injection",
                    "file": "app/users/service.py",
                    "line": 59,
                    "severity": "critical",
                    "description": "SQL injection in search_users_by_name",
                    "evidence": "f\"SELECT",
                    "recommended_fix": "Use parameterized query",
                }
            )
        if "app/inventory/service.py" in prompt_lower:
            bugs.append(
                {
                    "id": "MOCK-BUG-PAGE",
                    "kind": "off_by_one",
                    "file": "app/inventory/service.py",
                    "line": 126,
                    "severity": "medium",
                    "description": "Pagination off-by-one",
                    "evidence": "range(1, len(",
                    "recommended_fix": "Fix slice offset",
                }
            )
        if "app/auth/service.py" in prompt_lower:
            bugs.append(
                {
                    "id": "MOCK-BUG-LOG",
                    "kind": "logging",
                    "file": "app/auth/service.py",
                    "line": 21,
                    "severity": "high",
                    "description": "Credentials logged at INFO",
                    "evidence": "password=%s",
                    "recommended_fix": "Redact secrets from logs",
                }
            )
        return json.dumps({"bugs": bugs})

    @staticmethod
    def _is_fibonacci_planner(prompt_lower: str) -> bool:
        return (
            "create a plan for" in prompt_lower
            or ("fibonacci" in prompt_lower and _has_word(prompt_lower, "plan"))
        )

    @staticmethod
    def _is_fibonacci_code_gen(prompt_lower: str) -> bool:
        return "fibonacci" in prompt_lower and (
            _has_word(prompt_lower, "generate") or _has_word(prompt_lower, "code")
        )

    @staticmethod
    def _is_fibonacci_debug(prompt_lower: str) -> bool:
        return "fibonacci" not in prompt_lower and (
            _has_word(prompt_lower, "debug")
            or (_has_word(prompt_lower, "analyze") and "error" in prompt_lower)
        )

    @staticmethod
    def _is_fibonacci_fix_report(prompt_lower: str) -> bool:
        return "fibonacci" in prompt_lower and (
            _has_word(prompt_lower, "fix") or _has_word(prompt_lower, "report")
        )
