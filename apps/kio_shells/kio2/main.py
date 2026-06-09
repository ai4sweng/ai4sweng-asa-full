"""KIO2 — Planning Agent

Analyses the task description and repository, then tells the orchestrator
which KIOs to run by returning a `kio_sequence` in its response.

Pipeline: kio2 → kio3 → kio4 → kio5 → [HITL] → kio6 → kio7 → kio8
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import uvicorn
from loguru import logger

from kio_base import MessageEnvelope, make_kio_app
from shared.config import get_settings

KIO_ID = "kio2"
TITLE = "Planning Agent"

FULL_PIPELINE = ["kio2", "kio3", "kio4", "kio5", "kio6", "kio7", "kio8"]


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "")
    working_dir = payload.get("working_directory", "")

    logger.info("[kio2] Planning pipeline for: {}", description[:80])

    artifact_data = {
        "kio": KIO_ID,
        "pipeline": FULL_PIPELINE,
        "reasoning": (
            f"Standard bug-detection pipeline selected for task: '{description[:120]}'. "
            f"Repository: '{working_dir}'. "
            "Stages: repo analysis → test generation → bug detection → "
            "human approval → patch → test re-run → evidence report."
        ),
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "status": "DONE",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": artifact_data,
        # Tells the orchestrator to replace kio_sequence with the full pipeline.
        # Must include "kio2" as index-0 so the step counter stays consistent.
        "kio_sequence": FULL_PIPELINE,
        "message": (
            "Pipeline planned: kio3 → kio4 → kio5 → [HITL] → kio6 → kio7 → kio8"
        ),
    }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8012)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
