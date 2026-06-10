"""HTTP client for the LM Engine service."""

from __future__ import annotations


import httpx
from loguru import logger

from shared.config import get_settings
from shared.llm.llm_json_coerce import extract_json_object

_VALID_KIOS = {f"kio{n}" for n in range(2, 14)}
_FALLBACK_SEQUENCE = ["kio3", "kio5"]

# qwen3b sometimes returns duplicate KIOs or very long sequences.
_MAX_KIO_SEQUENCE_LEN = 8


class LmEngineClient:
    def __init__(self) -> None:
        cfg = get_settings()
        self._client = httpx.AsyncClient(
            base_url=cfg.lm_engine_url, timeout=float(cfg.lm_engine_client_timeout)
        )

    async def plan_workflow(self, description: str, session_id: str) -> tuple[list[str], str]:
        """Ask LM Engine which KIOs to run. Falls back to kio3→kio5 on any failure.

        Robust against common qwen3b hallucinations:
          • Markdown-fenced JSON     → extract_json_object strips fences
          • Python literals (None/True/False) → repair_json_text normalises
          • Truncated JSON           → _close_unclosed_json appends missing brackets
          • Wrong field names        → checked via .get() with defaults
          • Duplicate KIOs           → deduplicated while preserving order
          • Invalid KIO IDs          → filtered against _VALID_KIOS
          • Empty / oversized output → falls back to _FALLBACK_SEQUENCE
        """
        system = (
            "You are a workflow planner for an AI software-engineering platform. "
            "Given a task description, respond with ONLY a JSON object — no other text:\n"
            '{"kio_sequence": ["kio3", "kio5"], "reasoning": "one sentence"}\n'
            "Valid KIO IDs: kio2, kio3, kio4, kio5, kio6, kio7, kio8, kio9, "
            "kio10, kio11, kio12, kio13. "
            "Pick only those needed. Return at most 8 KIOs."
        )
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    "/llm/complete",
                    json={
                        "prompt": description,
                        "system": system,
                        "caller": "orchestrator-planner",
                    },
                )
                resp.raise_for_status()
                raw: str = resp.json().get("content", "")
                plan = extract_json_object(raw)  # robust multi-strategy parser
                if plan is None:
                    raise ValueError(f"LM returned non-JSON content: {raw[:200]!r}")

                # Normalise: accept "kio_sequence" or common alias "kios"
                seq_raw = plan.get("kio_sequence") or plan.get("kios") or []
                if not isinstance(seq_raw, list):
                    raise ValueError(f"kio_sequence is not a list: {seq_raw!r}")

                # Filter invalid IDs, deduplicate preserving order, cap length
                seen: set[str] = set()
                kios: list[str] = []
                for k in seq_raw:
                    k = str(k).strip().lower()
                    if k in _VALID_KIOS and k not in seen:
                        seen.add(k)
                        kios.append(k)
                        if len(kios) >= _MAX_KIO_SEQUENCE_LEN:
                            break

                reasoning: str = str(plan.get("reasoning", "")).strip()

                if not kios:
                    raise ValueError("LM returned no valid KIO IDs")

                logger.info(
                    "LM planned KIO sequence: {} (attempt {})",
                    " → ".join(k.upper() for k in kios),
                    attempt + 1,
                )
                return kios, reasoning

            except Exception as exc:
                logger.warning(
                    "LM planning attempt {}/{} failed ({}); {}",
                    attempt + 1,
                    2,
                    exc,
                    "retrying…" if attempt == 0 else "using fallback",
                )

        logger.warning(
            "LM planning failed after 2 attempts — falling back to {}",
            " → ".join(k.upper() for k in _FALLBACK_SEQUENCE),
        )
        return list(_FALLBACK_SEQUENCE), "Fallback: kio3 → kio5 (LM planning failed)"

    async def close(self) -> None:
        await self._client.aclose()


_client: LmEngineClient | None = None


def get_lm_client() -> LmEngineClient:
    global _client
    if _client is None:
        _client = LmEngineClient()
    return _client
