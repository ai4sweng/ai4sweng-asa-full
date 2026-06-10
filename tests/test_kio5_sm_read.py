"""KIO5 reads upstream artifacts from Session Manager (PostgreSQL gateway).

What this file proves
---------------------
KIOs have no DB driver or credentials.  They read prior-step artifacts by
calling the Session Manager HTTP API, which is the sole gateway to PostgreSQL.

The four tests here cover every branch of that read path in kio5:

  1. SM is called and findings are used       → normal happy-path
  2. last_artifact already has findings       → SM is NOT called (backward compat)
  3. SM is unreachable                        → kio5 degrades gracefully (no crash)
  4. Fetched findings appear in LLM prompt    → data actually flows end-to-end
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import respx

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "kio_shells"))

import kio5.main as kio5_main
from kio_base import MessageEnvelope

# ── helpers ───────────────────────────────────────────────────────────────────

SM_URL = "http://sm-test:8002"


def _envelope(
    session_id: str, artifact_ids: list[str], last_artifact: dict | None = None
) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=str(uuid.uuid4()),
        session_id=session_id,
        workflow_id=session_id,
        source="orchestrator",
        target="kio5",
        timestamp="2026-06-10T00:00:00+00:00",
        message_type="JOB_REQUEST",
        payload={
            "description": "security review",
            "last_artifact": last_artifact or {},
            "input_artifacts": [
                {"artifact_id": aid, "artifact_type": "json"} for aid in artifact_ids
            ],
            "priority": 5,
        },
    )


def _sm_artifact(session_id: str, artifact_id: str, findings: list) -> dict:
    """What PostgreSQL returns, serialised by Session Manager."""
    return {
        "artifact_id": artifact_id,
        "session_id": session_id,
        "producer_kio": "kio3",
        "artifact_type": "json",
        "artifact_data": {"findings": findings},
        "parent_artifact_id": None,
    }


def _mock_llm(bug_json: str | None = None) -> AsyncMock:
    resp = AsyncMock()
    resp.content = bug_json or json.dumps(
        {
            "summary": "1 sql injection confirmed",
            "confirmed_count": 1,
            "bugs": [
                {
                    "bug_id": "BUG-001",
                    "file": "app.py",
                    "line": 42,
                    "severity": "HIGH",
                    "kind": "sql_injection",
                    "description": "raw query built from user input",
                    "cwe": "CWE-89",
                    "suggested_fix": "use parameterised queries",
                    "confirmed": True,
                }
            ],
        }
    )
    provider = AsyncMock()
    provider.complete.return_value = resp
    return provider


def _mock_cfg():
    cfg = type("Cfg", (), {"session_manager_url": SM_URL})()
    return cfg


# ── Test 1: SM is called and findings flow into kio5 ─────────────────────────


async def test_kio5_fetches_findings_from_sm_when_last_artifact_empty():
    """With empty last_artifact, kio5 must call SM /batch and use the findings."""
    sid = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    findings = [{"file": "app.py", "line": 42, "kind": "sql_injection"}]

    with respx.mock:
        batch_route = respx.post(f"{SM_URL}/sessions/{sid}/artifacts/batch").mock(
            return_value=httpx.Response(200, json=[_sm_artifact(sid, aid, findings)])
        )

        with (
            patch.object(kio5_main, "_get_provider", return_value=_mock_llm()),
            patch("shared.config.get_settings", return_value=_mock_cfg()),
        ):
            result = await kio5_main.handler(_envelope(sid, [aid]))

    # SM was called — this is the PostgreSQL round-trip
    assert batch_route.called, "SessionReader must call SM /batch endpoint"

    # The artifact ID we asked for was in the request body
    body = json.loads(batch_route.calls[0].request.content)
    assert aid in body["artifact_ids"]

    # kio5 completed successfully and produced a REVIEW_REQUIRED
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["artifact_data"]["bug_count"] == 1


# ── Test 2: SM is NOT called when last_artifact already carries findings ──────


async def test_kio5_skips_sm_when_last_artifact_has_findings():
    """Inline findings take priority — no SM call needed (backward compat)."""
    sid = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    inline_findings = [{"file": "views.py", "line": 10, "kind": "xss"}]

    with respx.mock:
        batch_route = respx.post(f"{SM_URL}/sessions/{sid}/artifacts/batch").mock(
            return_value=httpx.Response(200, json=[])
        )  # would return empty anyway

        with (
            patch.object(kio5_main, "_get_provider", return_value=_mock_llm()),
            patch("shared.config.get_settings", return_value=_mock_cfg()),
        ):
            # last_artifact already contains findings → SM should NOT be called
            result = await kio5_main.handler(
                _envelope(sid, [aid], last_artifact={"findings": inline_findings})
            )

    assert not batch_route.called, "SM must not be called when last_artifact has findings"
    assert result["status"] == "REVIEW_REQUIRED"


# ── Test 3: SM unreachable — kio5 degrades gracefully ────────────────────────


async def test_kio5_sm_unavailable_falls_back_gracefully():
    """If SM is down, kio5 logs a warning and continues with empty findings."""
    sid = str(uuid.uuid4())
    aid = str(uuid.uuid4())

    with respx.mock:
        respx.post(f"{SM_URL}/sessions/{sid}/artifacts/batch").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        with (
            patch.object(
                kio5_main,
                "_get_provider",
                return_value=_mock_llm(
                    json.dumps({"summary": "no findings", "confirmed_count": 0, "bugs": []})
                ),
            ),
            patch("shared.config.get_settings", return_value=_mock_cfg()),
        ):
            result = await kio5_main.handler(_envelope(sid, [aid]))

    # kio5 must not raise — it degrades to 0 bugs
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["artifact_data"]["bug_count"] == 0


# ── Test 4: Findings from SM appear verbatim in the LLM prompt ───────────────


async def test_kio5_findings_from_sm_appear_in_llm_prompt():
    """End-to-end data flow: PostgreSQL → SM → SessionReader → LLM prompt."""
    sid = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    findings = [
        {"file": "auth.py", "line": 99, "kind": "insecure_deserialization"},
        {"file": "db.py", "line": 7, "kind": "sql_injection"},
    ]

    provider = _mock_llm(
        json.dumps(
            {
                "summary": "2 bugs",
                "confirmed_count": 2,
                "bugs": [
                    {
                        "bug_id": "BUG-001",
                        "file": "auth.py",
                        "line": 99,
                        "severity": "CRITICAL",
                        "kind": "insecure_deserialization",
                        "description": "pickle.loads on user input",
                        "cwe": "CWE-502",
                        "suggested_fix": "use json.loads",
                        "confirmed": True,
                    },
                    {
                        "bug_id": "BUG-002",
                        "file": "db.py",
                        "line": 7,
                        "severity": "HIGH",
                        "kind": "sql_injection",
                        "description": "raw query",
                        "cwe": "CWE-89",
                        "suggested_fix": "use parameterised queries",
                        "confirmed": True,
                    },
                ],
            }
        )
    )

    with respx.mock:
        respx.post(f"{SM_URL}/sessions/{sid}/artifacts/batch").mock(
            return_value=httpx.Response(200, json=[_sm_artifact(sid, aid, findings)])
        )

        with (
            patch.object(kio5_main, "_get_provider", return_value=provider),
            patch("shared.config.get_settings", return_value=_mock_cfg()),
        ):
            result = await kio5_main.handler(_envelope(sid, [aid]))

    # Both finding kinds must appear in the prompt the LLM actually received
    prompt_sent = provider.complete.call_args[0][0]
    assert "insecure_deserialization" in prompt_sent
    assert "sql_injection" in prompt_sent

    # Output reflects both confirmed bugs
    assert result["artifact_data"]["bug_count"] == 2
