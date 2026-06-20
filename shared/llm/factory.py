"""LLM provider factory — selects provider from LLM_PROVIDER env var."""

import logging

from shared.config import Settings, get_settings
from shared.llm.base import BaseLLMProvider
from shared.llm.claude_provider import ClaudeProvider
from shared.llm.mock import MockLLMProvider
from shared.llm.observed import ObservedLLMProvider
from shared.llm.ollama_provider import OllamaProvider, OllamaUnavailableError
from shared.llm.openai_provider import OpenAIProvider
from shared.llm.openrouter_provider import OpenRouterProvider
from shared.observability.observability import ObservabilityService

logger = logging.getLogger(__name__)


async def create_llm_provider(
    observability: ObservabilityService | None = None,
    *,
    settings: Settings | None = None,
    override: str = "",
) -> BaseLLMProvider:
    """
    Build the configured LLM provider wrapped with observability tracing.

    LLM_PROVIDER options: mock (default), ollama, openai, claude/anthropic

    Parameters
    ----------
    override : str
        Explicit provider name that bypasses LLM_PROVIDER env var.
        Used by the HITL fallback mechanism to retry with a different model.
    """
    cfg = settings or get_settings()
    provider = (override or cfg.llm_provider or "mock").strip().lower()

    inner: BaseLLMProvider

    if provider == "ollama":
        inner = OllamaProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.ollama_model,
            max_tokens=cfg.ollama_max_tokens,
        )
        try:
            await inner.health_check()  # type: ignore[attr-defined]
        except OllamaUnavailableError as exc:
            fallback = (override or cfg.llm_provider_fallback or "").strip().lower()
            if fallback and fallback != "ollama":
                logger.warning("Ollama unavailable — falling back to %s (%s)", fallback, exc)
                return await create_llm_provider(observability, settings=cfg, override=fallback)
            logger.error("%s", exc)
            raise SystemExit(1) from exc
        logger.info("Using Ollama model=%s base_url=%s", cfg.ollama_model, cfg.ollama_base_url)

    elif provider == "openai":
        if not cfg.openai_api_key:
            logger.error("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
            raise SystemExit(1)
        inner = OpenAIProvider(model=cfg.openai_model, api_key=cfg.openai_api_key)
        logger.info("Using OpenAI model=%s", cfg.openai_model)

    elif provider == "openrouter":
        if not cfg.openrouter_api_key:
            logger.error("LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set")
            raise SystemExit(1)
        inner = OpenRouterProvider(model=cfg.openrouter_model, api_key=cfg.openrouter_api_key)
        logger.info("Using OpenRouter model=%s", cfg.openrouter_model)

    elif provider in ("claude", "anthropic"):
        if not cfg.anthropic_api_key:
            logger.error("LLM_PROVIDER=claude but ANTHROPIC_API_KEY is not set")
            raise SystemExit(1)
        inner = ClaudeProvider(model=cfg.anthropic_model, api_key=cfg.anthropic_api_key)
        logger.info("Using Claude model=%s", cfg.anthropic_model)

    elif provider == "mock":
        inner = MockLLMProvider()
        logger.info("Using MockLLMProvider (default)")

    else:
        logger.error(
            "Unknown LLM_PROVIDER='%s'. Supported: mock, ollama, openai, openrouter, claude",
            provider,
        )
        raise SystemExit(1)

    return ObservedLLMProvider(inner, observability)
