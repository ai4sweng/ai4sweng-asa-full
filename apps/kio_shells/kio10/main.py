"""KIO10 — Energy-Efficiency with Model-Driven TinyML and LLMs

Analyses code and ML models for energy efficiency issues and recommends
TinyML optimisation techniques: quantisation, pruning, knowledge distillation,
and hardware-specific deployment strategies for edge/embedded targets.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))
sys.path.insert(0, str(Path(__file__).parents[3]))

import uvicorn
from loguru import logger

from kio_base import MessageEnvelope, make_kio_app, publish_progress
from shared.llm.factory import create_llm_provider
from shared.tools.repo_context_builder import RepoContextBuilder
from shared.config import get_settings

KIO_ID = "kio10"
TITLE = "Energy-Efficiency with Model-Driven TinyML and LLMs"

SYSTEM_PROMPT = """\
You are a TinyML and energy-efficiency expert specialising in deploying AI models
on resource-constrained edge and embedded devices.

Given source code, model files, or task descriptions, produce:
1. An energy efficiency assessment of the current code/model.
2. Specific TinyML optimisation recommendations ordered by impact:
   - Quantisation (int8, float16, dynamic range)
   - Pruning strategy (magnitude-based, structured, unstructured)
   - Knowledge distillation targets (teacher-student pairs)
   - Operator fusion and graph optimisation opportunities
   - Hardware target recommendations (MCU, NPU, GPU tier)
3. Estimated resource savings (memory reduction %, inference speedup %, energy reduction %).
4. Deployment checklist for the recommended target hardware.

Return ONLY valid JSON, no markdown fences:
{
  "summary": "overall energy efficiency assessment",
  "current_profile": {
    "estimated_params": 0,
    "estimated_flops": 0,
    "memory_mb": 0,
    "target_hardware": "unknown"
  },
  "recommendations": [
    {
      "technique": "int8_quantisation",
      "priority": "HIGH",
      "estimated_memory_reduction_pct": 75,
      "estimated_speedup_pct": 40,
      "estimated_energy_reduction_pct": 60,
      "implementation_steps": ["step 1", "step 2"],
      "tools": ["TensorFlow Lite", "ONNX Runtime"]
    }
  ],
  "deployment_targets": [
    {
      "device": "STM32H7",
      "feasibility": "HIGH",
      "notes": "Suitable after int8 quantisation"
    }
  ],
  "deployment_checklist": ["item1", "item2"],
  "confidence": 0.85
}"""


_provider = None
_provider_lock = asyncio.Lock()


async def _get_provider(override: str = ""):
    if override:
        return await create_llm_provider(override=override)
    global _provider
    if _provider is None:
        async with _provider_lock:
            if _provider is None:
                _provider = await create_llm_provider()
    return _provider


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")].strip()
    return text


async def _build_repo_context(repo_path: str) -> str:
    abs_path = Path(repo_path)
    if not abs_path.is_absolute():
        abs_path = Path.cwd() / abs_path
    if not abs_path.exists():
        return ""
    cfg = get_settings()
    builder = RepoContextBuilder(str(abs_path))
    inventory = builder.build_inventory()
    rel_paths = builder.select_analysis_files(inventory, max_files=cfg.repo_analysis_max_files)
    parts = []
    for rel_path in rel_paths:
        ctx = builder.read_file_context(rel_path)
        parts.append(f"## {rel_path}\n```\n{ctx.content}\n```")
    return "\n\n".join(parts)


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "Analyse for energy efficiency and TinyML suitability")
    repo_path = payload.get("working_directory", "") or get_settings().target_repo_path
    override = payload.get("llm_override", "")
    js = getattr(envelope, "_js", None)

    logger.info("[kio10] Energy analysis for: {}", description[:80])
    await publish_progress(KIO_ID, envelope.session_id, 10, "Loading code context", js)

    try:
        provider = await _get_provider(override)
        repo_context = await _build_repo_context(repo_path)

        await publish_progress(KIO_ID, envelope.session_id, 35, "Analysing energy profile", js)

        user_prompt = f"Task: {description}\nRepository: {repo_path or '(not specified)'}\n\n"
        if repo_context:
            user_prompt += f"Source code:\n{repo_context}\n\n"
        user_prompt += "Produce a TinyML energy efficiency analysis and recommendations."

        await publish_progress(
            KIO_ID, envelope.session_id, 55, "LLM generating recommendations", js
        )
        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        raw = _strip_fences(response.content)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {
                "summary": response.content[:500],
                "current_profile": {},
                "recommendations": [],
                "deployment_targets": [],
                "deployment_checklist": [],
                "confidence": 0.3,
            }

        rec_count = len(result.get("recommendations", []))
        await publish_progress(
            KIO_ID, envelope.session_id, 90, f"{rec_count} recommendation(s) generated", js
        )
        logger.info("[kio10] {} recommendation(s) produced", rec_count)

        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "summary": result.get("summary", ""),
                "current_profile": result.get("current_profile", {}),
                "recommendations": result.get("recommendations", []),
                "deployment_targets": result.get("deployment_targets", []),
                "deployment_checklist": result.get("deployment_checklist", []),
                "confidence": result.get("confidence", 1.0),
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": (f"Energy analysis complete — {rec_count} optimisation recommendation(s)."),
        }

    except Exception as exc:
        logger.exception("[kio10] failed: {}", exc)
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "error": str(exc),
                "recommendations": [],
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": f"KIO10 failed: {exc}",
        }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8020)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
