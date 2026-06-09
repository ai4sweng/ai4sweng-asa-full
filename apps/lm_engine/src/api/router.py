"""LM Engine inference router — delegates to shared.llm.factory."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from loguru import logger

from .schemas import CompleteRequest, CompleteResponse

router = APIRouter(prefix="/llm", tags=["llm"])

_provider = None
_provider_lock = asyncio.Lock()


async def _get_provider():
    """Return the singleton LLM provider, creating it once with a lock to prevent races."""
    global _provider
    if _provider is None:
        async with _provider_lock:
            if _provider is None:  # double-check after acquiring lock
                from shared.llm.factory import create_llm_provider
                _provider = await create_llm_provider()
    return _provider


@router.post("/complete", response_model=CompleteResponse)
async def complete(req: CompleteRequest):
    """
    Generate a completion from the configured LLM provider.

    Routes to Ollama (default), OpenAI, or Claude depending on LLM_PROVIDER env var.
    The provider is lazily initialized on the first request.
    """
    provider = await _get_provider()
    logger.info("LM inference caller={} task={}", req.caller, req.task_type)
    resp = await provider.complete(req.prompt, system=req.system)
    return CompleteResponse(
        content=resp.content,
        tokens_in=resp.tokens_in,
        tokens_out=resp.tokens_out,
        model=resp.model,
        latency_ms=resp.latency_ms,
    )
