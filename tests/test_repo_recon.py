"""Read-only repo recon: deterministic signals that ground agent selection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.orchestrator.src.engine.graph_nodes import make_nodes
from shared.tools.repo_recon import RepoSignals, format_signals, scan_repo


def _make_repo(root):
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("print('hi')\n")
    (root / "app" / "auth.py").write_text("def login(): ...\n")  # security surface
    (root / "tests").mkdir()
    (root / "tests" / "test_main.py").write_text("def test_x(): ...\n")
    (root / "requirements.txt").write_text("fastapi\n")
    (root / "web.ts").write_text("export const x = 1\n")
    # Vendored dir must be ignored entirely.
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("x\n")
    return root


def test_scan_repo_missing_path():
    assert scan_repo("/no/such/path/here").exists is False


def test_scan_repo_extracts_signals(tmp_path):
    sig = scan_repo(str(_make_repo(tmp_path)))

    assert sig.exists is True
    langs = dict(sig.languages)
    assert langs.get("python") == 3  # main, auth, test_main
    assert langs.get("typescript") == 1
    assert "javascript" not in langs  # node_modules pruned
    assert sig.has_tests is True
    assert sig.dependency_files == ["requirements.txt"]
    assert sig.security_sensitive is True
    assert any("auth.py" in s for s in sig.security_surfaces)


def test_format_signals_empty_when_no_repo():
    assert format_signals(RepoSignals(exists=False)) == ""
    assert format_signals(RepoSignals(exists=True, file_count=0)) == ""


def test_format_signals_renders_block(tmp_path):
    block = format_signals(scan_repo(str(_make_repo(tmp_path))))
    assert "Repository signals" in block
    assert "has tests: true" in block
    assert "security-sensitive surfaces: true" in block


@pytest.mark.asyncio
async def test_plan_node_feeds_recon_signals_to_planner(tmp_path):
    _make_repo(tmp_path)
    lm = AsyncMock()
    lm.assess_clarification = AsyncMock(return_value=None)
    lm.plan_workflow = AsyncMock(return_value=(["kio3", "kio5"], "reason", False, []))
    lm.critique_plan = AsyncMock(return_value=None)
    bus = MagicMock()
    nodes = make_nodes(AsyncMock(), AsyncMock(), lm, bus, {"s1": {"status": "QUEUED"}})

    state = {
        "session_id": "s1",
        "description": "analyze this repo for security issues",
        "working_directory": str(tmp_path),
        "kio_sequence": [],
        "initial_context": {},
        "clarify_attempts": 0,
    }

    await nodes["plan"](state)

    # Recon ran, was surfaced as an event, and reached the planner as `signals`.
    assert "PLAN_RECON" in str(bus.method_calls)
    kwargs = lm.plan_workflow.call_args.kwargs
    assert kwargs.get("signals")
    assert "Repository signals" in kwargs["signals"]
    assert "security-sensitive surfaces: true" in kwargs["signals"]
