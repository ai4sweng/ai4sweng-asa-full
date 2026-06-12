"""Comprehensive handler tests for all 13 KIOs.

Every handler is imported directly (no HTTP, no NATS, no real LLM).
The LLM provider is mocked in every test via patch("kioN.main._get_provider").
Pipeline chain tests verify artifact pass-through between consecutive KIOs.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "kio_shells"))

from kio_base import MessageEnvelope  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def _envelope(kio_id: str, payload: dict | None = None) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        workflow_id="wf-test",
        source="orchestrator",
        target=kio_id,
        timestamp="2026-06-11T00:00:00+00:00",
        message_type="JOB_REQUEST",
        payload=payload or {},
    )


def _mock_provider(content: str) -> AsyncMock:
    provider = AsyncMock()
    resp = MagicMock()
    resp.content = content
    resp.latency_ms = 50.0
    resp.tokens_in = 10
    resp.tokens_out = 20
    provider.complete.return_value = resp
    return provider


def _reset(mod):
    """Reset module-level _provider singleton."""
    mod._provider = None


@pytest.fixture(autouse=True)
def _stub_jetstream():
    """Stop handlers reaching for real NATS/JetStream.

    Handlers call ``get_jetstream()`` for ``publish_progress``; with no broker up
    that connect attempt blocks ~20s per call before timing out. Patch the source
    symbol (handlers re-import it locally at call time) so it returns instantly.
    """
    js = MagicMock()
    js.publish = AsyncMock()
    with patch(
        "shared.messaging.jetstream.get_jetstream", new=AsyncMock(return_value=js)
    ):
        yield


# ── KIO1 — Prompt Router ──────────────────────────────────────────────────────


class TestKio1Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio1.main as m
        _reset(m)
        yield
        _reset(m)

    async def test_returns_done(self):
        from kio1.main import handler
        resp = json.dumps({
            "kio_sequence": ["kio1", "kio5"],
            "hitl_after": ["kio5"],
            "description": "detect bugs",
            "reasoning": "code + bug keywords",
        })
        with patch("kio1.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio1", {"description": "find bugs in my code"}))
        assert result["status"] == "DONE"

    async def test_kio_sequence_starts_with_kio1(self):
        from kio1.main import handler
        resp = json.dumps({
            "kio_sequence": ["kio1", "kio5"],
            "hitl_after": ["kio5"],
            "description": "detect",
            "reasoning": "ok",
        })
        with patch("kio1.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio1", {"description": "find bugs"}))
        assert result["kio_sequence"][0] == "kio1"

    async def test_fallback_on_llm_error(self):
        from kio1.main import handler
        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("LLM down")
        with patch("kio1.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio1", {"description": "something"}))
        assert result["status"] == "DONE"
        assert result["artifact_data"]["used_fallback_route"] is True

    async def test_fallback_on_invalid_sequence(self):
        from kio1.main import handler
        resp = json.dumps({
            "kio_sequence": ["kio5"],  # missing kio1 at index 0
            "hitl_after": [],
            "description": "bad",
            "reasoning": "bad",
        })
        with patch("kio1.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio1", {"description": "check"}))
        assert result["artifact_data"]["used_fallback_route"] is True

    async def test_artifact_data_has_required_keys(self):
        from kio1.main import handler
        resp = json.dumps({
            "kio_sequence": ["kio1", "kio3", "kio8"],
            "hitl_after": [],
            "description": "analyse",
            "reasoning": "ok",
        })
        with patch("kio1.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio1", {"description": "analyse repo"}))
        data = result["artifact_data"]
        for key in ("kio_sequence", "hitl_after", "intent_description", "produced_at"):
            assert key in data

    async def test_hitl_after_in_response(self):
        from kio1.main import handler
        resp = json.dumps({
            "kio_sequence": ["kio1", "kio5", "kio6", "kio7"],
            "hitl_after": ["kio5"],
            "description": "patch bugs",
            "reasoning": "fix",
        })
        with patch("kio1.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio1", {"description": "fix and patch"}))
        assert "kio5" in result["hitl_after"]


# ── KIO3 — Repo Analyzer ──────────────────────────────────────────────────────


class TestKio3Handler:
    async def test_returns_done(self):
        from kio3.main import handler
        # Non-existent path → fallback findings
        result = await handler(_envelope("kio3", {"working_directory": "/nonexistent/path"}))
        assert result["status"] == "DONE"

    async def test_fallback_findings_when_repo_missing(self):
        from kio3.main import handler
        result = await handler(_envelope("kio3", {"working_directory": "/nonexistent/path"}))
        assert result["artifact_data"]["finding_count"] > 0
        assert len(result["artifact_data"]["findings"]) > 0

    async def test_findings_have_required_fields(self):
        from kio3.main import handler
        result = await handler(_envelope("kio3", {"working_directory": "/nonexistent/path"}))
        for finding in result["artifact_data"]["findings"]:
            assert "file" in finding
            assert "severity" in finding
            assert "description" in finding

    async def test_artifact_data_has_kio_key(self):
        from kio3.main import handler
        result = await handler(_envelope("kio3", {}))
        assert result["artifact_data"]["kio"] == "kio3"

    async def test_exception_falls_back_gracefully(self):
        from kio3.main import handler
        with patch("kio3.main._get_provider", side_effect=RuntimeError("provider fail")):
            result = await handler(_envelope("kio3", {"working_directory": "/nonexistent"}))
        assert result["status"] == "DONE"
        assert len(result["artifact_data"]["findings"]) > 0


# ── KIO4 — Test Generator ─────────────────────────────────────────────────────


class TestKio4Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio4.main as m
        _reset(m)
        yield
        _reset(m)

    async def test_returns_done(self):
        from kio4.main import handler
        llm_resp = (
            "### tests/test_security.py\n"
            "```python\ndef test_example():\n    assert True\n```"
        )
        with patch("kio4.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio4", {
                "description": "generate tests",
                "last_artifact": {"findings": [{"file": "app.py", "line": 10, "severity": "HIGH"}]},
            }))
        assert result["status"] == "DONE"

    async def test_parses_code_blocks_into_files(self):
        from kio4.main import handler
        llm_resp = (
            "### tests/test_security.py\n"
            "```python\ndef test_sql_injection(client):\n    assert True\n```\n\n"
            "### tests/conftest.py\n"
            "```python\nimport pytest\n```"
        )
        with patch("kio4.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio4", {
                "description": "tests",
                "last_artifact": {"findings": []},
            }))
        assert result["artifact_data"]["file_count"] == 2

    async def test_findings_passed_through_in_artifact(self):
        from kio4.main import handler
        findings = [{"file": "app.py", "line": 5, "kind": "xss"}]
        llm_resp = "### tests/test_s.py\n```python\ndef test_x():\n    pass\n```"
        with patch("kio4.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio4", {
                "description": "generate",
                "last_artifact": {"findings": findings},
            }))
        assert result["artifact_data"]["findings"] == findings

    async def test_fallback_on_llm_error(self):
        from kio4.main import handler
        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("LLM down")
        with patch("kio4.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio4", {
                "description": "tests",
                "last_artifact": {"findings": []},
            }))
        assert result["status"] == "DONE"
        assert result["artifact_data"]["file_count"] == 0

    async def test_fallback_plain_code_block(self):
        from kio4.main import handler
        # No ### headers — response is a bare code block with def test_
        llm_resp = "```python\ndef test_something():\n    assert 1 == 1\n```"
        with patch("kio4.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio4", {
                "description": "tests",
                "last_artifact": {"findings": []},
            }))
        assert result["status"] == "DONE"


# ── KIO6 — Patch Agent ────────────────────────────────────────────────────────


class TestKio6Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio6.main as m
        _reset(m)
        yield
        _reset(m)

    async def test_returns_done(self):
        from kio6.main import handler
        llm_resp = (
            "### app/main.py\n"
            "```python\ndef safe_query(uid):\n    return db.execute('SELECT * FROM u WHERE id=?', uid)\n```"
        )
        with patch("kio6.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio6", {
                "description": "fix bugs",
                "last_artifact": {
                    "bugs": [{"bug_id": "BUG-001", "file": "app/main.py", "line": 10}],
                    "test_files": [],
                },
            }))
        assert result["status"] == "DONE"

    async def test_patches_parsed_from_code_blocks(self):
        from kio6.main import handler
        llm_resp = (
            "### app/main.py\n```python\n# fixed\n```\n\n"
            "### app/auth.py\n```python\n# secured\n```"
        )
        with patch("kio6.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio6", {
                "description": "patch",
                "last_artifact": {
                    "bugs": [{"bug_id": "B1", "file": "app/main.py"}],
                    "test_files": [],
                },
            }))
        assert result["artifact_data"]["patch_count"] == 2

    async def test_test_files_passed_through(self):
        from kio6.main import handler
        test_files = [{"path": "tests/test_security.py", "content": "def test_x(): pass"}]
        llm_resp = "### app/main.py\n```python\n# fix\n```"
        with patch("kio6.main._get_provider", return_value=_mock_provider(llm_resp)):
            result = await handler(_envelope("kio6", {
                "description": "patch",
                "last_artifact": {
                    "bugs": [],
                    "test_files": test_files,
                },
            }))
        assert result["artifact_data"]["test_files"] == test_files

    async def test_error_still_returns_done(self):
        from kio6.main import handler
        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("LLM down")
        with patch("kio6.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio6", {
                "description": "patch",
                "last_artifact": {"bugs": [], "test_files": []},
            }))
        assert result["status"] == "DONE"
        assert result["artifact_data"]["patch_count"] == 0


# ── KIO7 — Test Re-runner ─────────────────────────────────────────────────────


class TestKio7Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio7.main as m
        _reset(m)
        yield
        _reset(m)

    async def test_returns_done_when_no_repo(self):
        from kio7.main import handler
        result = await handler(_envelope("kio7", {
            "working_directory": "/nonexistent/repo",
            "last_artifact": {"patches": [], "test_files": []},
        }))
        assert result["status"] == "DONE"

    async def test_message_present(self):
        from kio7.main import handler
        result = await handler(_envelope("kio7", {
            "working_directory": "/nonexistent/repo",
            "last_artifact": {"patches": [], "test_files": []},
        }))
        assert "message" in result
        assert isinstance(result["message"], str)

    async def test_artifact_data_has_kio_key(self):
        from kio7.main import handler
        result = await handler(_envelope("kio7", {
            "working_directory": "/nonexistent/repo",
            "last_artifact": {"patches": [], "test_files": []},
        }))
        assert result["artifact_data"]["kio"] == "kio7"

    async def test_skips_pytest_when_no_patches_and_no_repo(self):
        from kio7.main import handler
        result = await handler(_envelope("kio7", {
            "working_directory": "",
            "last_artifact": {"patches": [], "test_files": []},
        }))
        # Without a repo, handler returns early with DONE and no pytest data
        assert result["status"] == "DONE"


# ── KIO9 — Code Generator ─────────────────────────────────────────────────────


class TestKio9Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio9.main as m
        _reset(m)
        yield
        _reset(m)

    async def test_returns_done(self):
        from kio9.main import handler
        with patch("kio9.main._get_provider", return_value=_mock_provider("def hello(): pass")):
            result = await handler(_envelope("kio9", {"description": "write a hello function"}))
        assert result["status"] == "DONE"

    async def test_code_in_artifact(self):
        from kio9.main import handler
        with patch("kio9.main._get_provider", return_value=_mock_provider("def hello(): pass")):
            result = await handler(_envelope("kio9", {"description": "generate code"}))
        assert "code" in result["artifact_data"]
        assert len(result["artifact_data"]["code"]) > 0

    async def test_strips_markdown_fences(self):
        from kio9.main import handler
        fenced = "```python\ndef greet():\n    return 'hi'\n```"
        with patch("kio9.main._get_provider", return_value=_mock_provider(fenced)):
            result = await handler(_envelope("kio9", {"description": "generate"}))
        code = result["artifact_data"]["code"]
        assert not code.startswith("```")

    async def test_error_produces_fallback_code(self):
        from kio9.main import handler
        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("LLM down")
        with patch("kio9.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio9", {"description": "generate"}))
        assert result["status"] == "DONE"
        assert "# Code generation failed" in result["artifact_data"]["code"]

    async def test_uses_prompt_from_last_artifact(self):
        from kio9.main import handler
        captured = {}

        async def fake_complete(prompt, **_):
            captured["prompt"] = prompt
            r = MagicMock()
            r.content = "def x(): pass"
            r.latency_ms = 10.0
            r.tokens_in = 5
            r.tokens_out = 5
            return r

        p = AsyncMock()
        p.complete.side_effect = fake_complete

        with patch("kio9.main._get_provider", return_value=p):
            await handler(_envelope("kio9", {
                "description": "ignored",
                "last_artifact": {"prompt": "write a sorting algorithm"},
            }))
        assert "sorting algorithm" in captured["prompt"]


# ── KIO10 — Energy Efficiency ─────────────────────────────────────────────────


class TestKio10Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio10.main as m
        _reset(m)
        yield
        _reset(m)

    async def test_returns_done(self):
        from kio10.main import handler
        resp = json.dumps({
            "summary": "Low energy usage",
            "current_profile": {},
            "recommendations": [{"title": "Use uint8 instead of float32"}],
            "deployment_targets": ["Arduino"],
            "deployment_checklist": [],
            "confidence": 0.9,
        })
        with patch("kio10.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio10", {"description": "energy audit"}))
        assert result["status"] == "DONE"

    async def test_recommendations_in_artifact(self):
        from kio10.main import handler
        resp = json.dumps({
            "summary": "ok",
            "current_profile": {},
            "recommendations": [{"title": "quantize"}],
            "deployment_targets": [],
            "deployment_checklist": [],
            "confidence": 0.8,
        })
        with patch("kio10.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio10", {"description": "audit"}))
        assert isinstance(result["artifact_data"]["recommendations"], list)

    async def test_non_json_response_handled(self):
        from kio10.main import handler
        with patch("kio10.main._get_provider", return_value=_mock_provider("Plain text response")):
            result = await handler(_envelope("kio10", {"description": "audit"}))
        assert result["status"] == "DONE"
        assert "summary" in result["artifact_data"]

    async def test_error_returns_done(self):
        from kio10.main import handler
        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("LLM fail")
        with patch("kio10.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio10", {"description": "audit"}))
        assert result["status"] == "DONE"


# ── KIO11 — Test Automation Tool ──────────────────────────────────────────────


class TestKio11Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio11.main as m
        m._provider = None
        yield
        m._provider = None

    async def test_returns_done(self):
        from kio11.main import handler
        resp = json.dumps({
            "summary": "Generated test suite",
            "test_count": 5,
            "files": [{"path": "tests/test_api.py", "content": "def test_x(): pass"}],
        })
        with patch("kio11.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio11", {"description": "generate tests"}))
        assert result["status"] == "DONE"

    async def test_files_in_artifact(self):
        from kio11.main import handler
        resp = json.dumps({
            "summary": "Done",
            "test_count": 1,
            "files": [{"path": "tests/test_a.py", "content": "def test_a(): pass"}],
        })
        with patch("kio11.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio11", {"description": "generate"}))
        assert isinstance(result["artifact_data"]["files"], list)

    async def test_non_json_response_handled(self):
        from kio11.main import handler
        with patch("kio11.main._get_provider", return_value=_mock_provider("raw text here")):
            result = await handler(_envelope("kio11", {"description": "test"}))
        assert result["status"] == "DONE"

    async def test_error_returns_done_with_empty_files(self):
        from kio11.main import handler
        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("fail")
        with patch("kio11.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio11", {"description": "test"}))
        assert result["status"] == "DONE"
        assert result["artifact_data"]["files"] == []


# ── KIO12 — Cybersecurity ─────────────────────────────────────────────────────


class TestKio12Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio12.main as m
        _reset(m)
        yield
        _reset(m)

    async def test_returns_done(self):
        from kio12.main import handler
        resp = json.dumps({
            "summary": "2 OWASP findings",
            "vulnerabilities": [{"id": "A1", "name": "SQLi"}],
            "files": [{"path": "app/main.py", "content": "# hardened"}],
        })
        with patch("kio12.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio12", {"description": "OWASP audit"}))
        assert result["status"] == "DONE"

    async def test_vulnerabilities_in_artifact(self):
        from kio12.main import handler
        resp = json.dumps({
            "summary": "found issues",
            "vulnerabilities": [{"id": "A3", "name": "XSS"}],
            "files": [],
        })
        with patch("kio12.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio12", {"description": "audit"}))
        assert isinstance(result["artifact_data"]["vulnerabilities"], list)

    async def test_inline_code_path(self):
        from kio12.main import handler
        resp = json.dumps({"summary": "clean", "vulnerabilities": [], "files": []})
        with patch("kio12.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio12", {
                "description": "audit inline",
                "code": "def login(user, pw): return db.execute(f'SELECT * FROM users WHERE user={user}')",
            }))
        assert result["status"] == "DONE"
        assert result["artifact_data"]["vulnerability_count"] == 0

    async def test_non_json_response_handled(self):
        from kio12.main import handler
        with patch("kio12.main._get_provider", return_value=_mock_provider("summary only text")):
            result = await handler(_envelope("kio12", {"description": "audit"}))
        assert result["status"] == "DONE"

    async def test_error_returns_done_with_empty_files(self):
        from kio12.main import handler
        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("fail")
        with patch("kio12.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio12", {"description": "audit"}))
        assert result["status"] == "DONE"
        assert result["artifact_data"]["files"] == []


# ── KIO13 — Developer Training ────────────────────────────────────────────────


class TestKio13Handler:
    @pytest.fixture(autouse=True)
    def reset(self):
        import kio13.main as m
        _reset(m)
        yield
        _reset(m)

    async def test_returns_done(self):
        from kio13.main import handler
        resp = json.dumps({
            "summary": "Training ready",
            "skill_gaps": ["SQL injection prevention"],
            "modules": [{"title": "Secure coding", "content": "..."}],
            "exercises": [],
            "reading_list": [],
            "quiz": [{"q": "What is SQLi?", "a": "Injection attack"}],
        })
        with patch("kio13.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio13", {"description": "training for team"}))
        assert result["status"] == "DONE"

    async def test_modules_in_artifact(self):
        from kio13.main import handler
        resp = json.dumps({
            "summary": "ok",
            "skill_gaps": [],
            "modules": [{"title": "M1", "content": "..."}],
            "exercises": [],
            "reading_list": [],
            "quiz": [],
        })
        with patch("kio13.main._get_provider", return_value=_mock_provider(resp)):
            result = await handler(_envelope("kio13", {"description": "training"}))
        assert isinstance(result["artifact_data"]["modules"], list)

    async def test_non_json_response_handled(self):
        from kio13.main import handler
        with patch("kio13.main._get_provider", return_value=_mock_provider("Here are your modules:")):
            result = await handler(_envelope("kio13", {"description": "training"}))
        assert result["status"] == "DONE"

    async def test_error_returns_done_with_empty_modules(self):
        from kio13.main import handler
        broken = AsyncMock()
        broken.complete.side_effect = RuntimeError("fail")
        with patch("kio13.main._get_provider", return_value=broken):
            result = await handler(_envelope("kio13", {"description": "training"}))
        assert result["status"] == "DONE"
        assert result["artifact_data"]["modules"] == []


# ── Pipeline chain tests ───────────────────────────────────────────────────────


class TestPipelineChains:
    """Simulate consecutive KIOs receiving artifacts from the previous step."""

    async def test_kio1_to_kio5_chain(self):
        """kio1 routes → kio5 receives its artifact as last_artifact."""
        import kio1.main as m1
        import kio5.main as m5
        m1._provider = None
        m5._provider = None

        kio1_resp = json.dumps({
            "kio_sequence": ["kio1", "kio5"],
            "hitl_after": ["kio5"],
            "description": "detect bugs",
            "reasoning": "code + bug keywords",
        })

        with patch("kio1.main._get_provider", return_value=_mock_provider(kio1_resp)):
            from kio1.main import handler as h1
            kio1_result = await h1(_envelope("kio1", {
                "description": "find bugs",
                "initial_context": {"code": "x = eval(input())"},
            }))

        assert kio1_result["status"] == "DONE"

        kio5_resp = json.dumps({
            "summary": "One eval() injection",
            "confirmed_count": 1,
            "bugs": [{"bug_id": "BUG-001", "file": "script.py", "line": 1,
                      "severity": "CRITICAL", "kind": "code_injection",
                      "description": "eval() with user input", "cwe": "CWE-94",
                      "suggested_fix": "Avoid eval().", "confirmed": True}],
        })

        with patch("kio5.main._get_provider", return_value=_mock_provider(kio5_resp)):
            from kio5.main import handler as h5
            kio5_result = await h5(_envelope("kio5", {
                "description": "detect bugs",
                "last_artifact": kio1_result["artifact_data"],
            }))

        assert kio5_result["status"] == "REVIEW_REQUIRED"
        assert kio5_result["artifact_data"]["bug_count"] == 1

    async def test_kio3_to_kio4_findings_passthrough(self):
        """kio3 findings appear in kio4's artifact unchanged."""
        from kio3.main import handler as h3

        kio3_result = await h3(_envelope("kio3", {"working_directory": "/nonexistent"}))
        original_findings = kio3_result["artifact_data"]["findings"]

        import kio4.main as m4
        m4._provider = None

        llm_resp = "### tests/test_security.py\n```python\ndef test_x():\n    pass\n```"
        with patch("kio4.main._get_provider", return_value=_mock_provider(llm_resp)):
            from kio4.main import handler as h4
            kio4_result = await h4(_envelope("kio4", {
                "description": "generate tests",
                "last_artifact": kio3_result["artifact_data"],
            }))

        assert kio4_result["artifact_data"]["findings"] == original_findings

    async def test_kio4_to_kio5_findings_passthrough(self):
        """kio4 passes its findings through to kio5 as last_artifact."""
        import kio4.main as m4
        import kio5.main as m5
        m4._provider = None
        m5._provider = None

        findings = [{"file": "app.py", "line": 42, "kind": "sql_injection"}]
        kio4_resp = "### tests/test_s.py\n```python\ndef test_sqli(client):\n    pass\n```"

        with patch("kio4.main._get_provider", return_value=_mock_provider(kio4_resp)):
            from kio4.main import handler as h4
            kio4_result = await h4(_envelope("kio4", {
                "description": "generate",
                "last_artifact": {"findings": findings},
            }))

        kio5_resp = json.dumps({
            "summary": "confirmed",
            "confirmed_count": 1,
            "bugs": [{"bug_id": "BUG-001", "file": "app.py", "line": 42,
                      "severity": "HIGH", "kind": "sql_injection",
                      "description": "SQLi", "cwe": "CWE-89",
                      "suggested_fix": "parameterise", "confirmed": True}],
        })

        with patch("kio5.main._get_provider", return_value=_mock_provider(kio5_resp)):
            from kio5.main import handler as h5
            kio5_result = await h5(_envelope("kio5", {
                "description": "validate",
                "last_artifact": kio4_result["artifact_data"],
            }))

        assert kio5_result["status"] == "REVIEW_REQUIRED"
        assert kio5_result["artifact_data"]["bug_count"] >= 1

    async def test_kio5_to_kio6_bugs_passthrough(self):
        """kio5 bugs appear in kio6's last_artifact for patching."""
        import kio5.main as m5
        import kio6.main as m6
        m5._provider = None
        m6._provider = None

        kio5_resp = json.dumps({
            "summary": "one bug",
            "confirmed_count": 1,
            "bugs": [{"bug_id": "BUG-001", "file": "app/main.py", "line": 10,
                      "severity": "HIGH", "kind": "sql_injection",
                      "description": "SQLi", "cwe": "CWE-89",
                      "suggested_fix": "parameterise", "confirmed": True}],
        })

        with patch("kio5.main._get_provider", return_value=_mock_provider(kio5_resp)):
            from kio5.main import handler as h5
            kio5_result = await h5(_envelope("kio5", {
                "description": "validate",
                "last_artifact": {"findings": [], "code": "x=1"},
            }))

        kio6_resp = "### app/main.py\n```python\n# parameterised query\n```"

        with patch("kio6.main._get_provider", return_value=_mock_provider(kio6_resp)):
            from kio6.main import handler as h6
            kio6_result = await h6(_envelope("kio6", {
                "description": "patch",
                "working_directory": "/nonexistent",
                "last_artifact": kio5_result["artifact_data"],
            }))

        assert kio6_result["status"] == "DONE"
        assert kio6_result["artifact_data"]["patch_count"] >= 1

    async def test_kio6_to_kio7_no_repo_early_exit(self):
        """kio7 receives kio6 patches but exits cleanly when repo is not mounted."""
        import kio6.main as m6
        m6._provider = None

        kio6_resp = "### app/main.py\n```python\n# fix\n```"
        with patch("kio6.main._get_provider", return_value=_mock_provider(kio6_resp)):
            from kio6.main import handler as h6
            kio6_result = await h6(_envelope("kio6", {
                "description": "patch",
                "working_directory": "/nonexistent",
                "last_artifact": {
                    "bugs": [{"bug_id": "B1", "file": "app/main.py"}],
                    "test_files": [],
                },
            }))

        from kio7.main import handler as h7
        kio7_result = await h7(_envelope("kio7", {
            "working_directory": "/nonexistent",
            "last_artifact": kio6_result["artifact_data"],
        }))

        assert kio7_result["status"] == "DONE"

    async def test_kio9_to_kio5_code_generation_then_bug_detect(self):
        """kio9 generates code → kio5 analyses it in direct-code mode."""
        import kio9.main as m9
        import kio5.main as m5
        m9._provider = None
        m5._provider = None

        kio9_code = "def login(user, pw):\n    return db.execute(f'SELECT * FROM users WHERE user={user}')"

        with patch("kio9.main._get_provider", return_value=_mock_provider(kio9_code)):
            from kio9.main import handler as h9
            kio9_result = await h9(_envelope("kio9", {
                "description": "write login function",
                "last_artifact": {"prompt": "write a login function"},
            }))

        assert kio9_result["artifact_data"]["code"]

        kio5_resp = json.dumps({
            "summary": "SQLi in login",
            "confirmed_count": 1,
            "bugs": [{"bug_id": "BUG-001", "file": "script.py", "line": 2,
                      "severity": "CRITICAL", "kind": "sql_injection",
                      "description": "f-string in SQL", "cwe": "CWE-89",
                      "suggested_fix": "Use parameterised queries.", "confirmed": True}],
        })

        with patch("kio5.main._get_provider", return_value=_mock_provider(kio5_resp)):
            from kio5.main import handler as h5
            kio5_result = await h5(_envelope("kio5", {
                "description": "detect bugs",
                "last_artifact": kio9_result["artifact_data"],
            }))

        assert kio5_result["status"] == "REVIEW_REQUIRED"
        assert kio5_result["artifact_data"]["bug_count"] == 1

    async def test_full_pipeline_kio1_kio3_kio4_kio5_kio8(self):
        """Smoke test: kio1→kio3→kio4→kio5→kio8 all return DONE/REVIEW_REQUIRED."""
        import kio1.main as m1, kio4.main as m4, kio5.main as m5, kio8.main as m8
        for m in (m1, m4, m5, m8):
            m._provider = None

        # kio1 routes
        kio1_resp = json.dumps({
            "kio_sequence": ["kio1", "kio3", "kio4", "kio5", "kio8"],
            "hitl_after": ["kio5"],
            "description": "full pipeline",
            "reasoning": "full",
        })
        from kio1.main import handler as h1
        with patch("kio1.main._get_provider", return_value=_mock_provider(kio1_resp)):
            r1 = await h1(_envelope("kio1", {"description": "full pipeline"}))
        assert r1["status"] == "DONE"

        # kio3 fallback (no real repo)
        from kio3.main import handler as h3
        r3 = await h3(_envelope("kio3", {"working_directory": "/nonexistent"}))
        assert r3["status"] == "DONE"

        # kio4 generates tests
        kio4_resp = "### tests/test_s.py\n```python\ndef test_x(): pass\n```"
        from kio4.main import handler as h4
        with patch("kio4.main._get_provider", return_value=_mock_provider(kio4_resp)):
            r4 = await h4(_envelope("kio4", {
                "description": "gen tests",
                "last_artifact": r3["artifact_data"],
            }))
        assert r4["status"] == "DONE"

        # kio5 validates
        kio5_resp = json.dumps({
            "summary": "1 bug",
            "confirmed_count": 1,
            "bugs": [{"bug_id": "BUG-001", "file": "app/main.py", "line": 23,
                      "severity": "HIGH", "kind": "sql_injection",
                      "description": "raw SQL", "cwe": "CWE-89",
                      "suggested_fix": "parameterise", "confirmed": True}],
        })
        from kio5.main import handler as h5
        with patch("kio5.main._get_provider", return_value=_mock_provider(kio5_resp)):
            r5 = await h5(_envelope("kio5", {
                "description": "validate",
                "last_artifact": r4["artifact_data"],
            }))
        assert r5["status"] == "REVIEW_REQUIRED"

        # kio8 final report
        kio8_resp = json.dumps({
            "executive_summary": "One bug found and addressed.",
            "findings": ["CWE-89 SQL injection — patched"],
            "remediation": "Parameterised queries added.",
            "test_evidence": "10/10 tests passed.",
            "verdict": "PASS",
            "verdict_reason": "All tests green.",
        })
        from kio8.main import handler as h8
        with patch("kio8.main._get_provider", return_value=_mock_provider(kio8_resp)):
            r8 = await h8(_envelope("kio8", {
                "description": "report",
                "last_artifact": r5["artifact_data"],
            }))
        assert r8["status"] == "DONE"
        assert r8["artifact_data"]["report"]["verdict"] == "PASS"
