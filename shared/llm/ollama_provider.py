"""
Ollama local LLM provider — uses the Ollama HTTP API (/api/chat).

Token counts prefer Ollama's prompt_eval_count / eval_count; falls back to
_estimate_tokens when the daemon omits counts (common on some models).
"""

import logging
import time
from typing import Any

import httpx

from shared.llm.base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class OllamaUnavailableError(RuntimeError):
    """Raised when Ollama cannot be reached or the configured model is missing."""


class OllamaProvider(BaseLLMProvider):
    """Local Ollama model integration via /api/chat."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:3b",
        max_tokens: int = 2048,
        timeout_sec: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._timeout_sec = timeout_sec

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        return self._model

    async def health_check(self) -> None:
        """
        Verify Ollama is running and the configured model is available.

        Raises OllamaUnavailableError with actionable guidance on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
                tags = response.json()
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(
                f"Cannot connect to Ollama at {self._base_url}. "
                "Start the daemon with: open -a Ollama  (or: ollama serve)"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(
                f"Ollama health check failed at {self._base_url}: {exc}"
            ) from exc

        model_names = {m.get("name", "") for m in tags.get("models", [])}
        if self._model not in model_names:
            available = ", ".join(sorted(model_names)) or "(none)"
            raise OllamaUnavailableError(
                f"Model '{self._model}' is not available in Ollama. "
                f"Run: ollama pull {self._model}\n"
                f"Currently installed models: {available}"
            )

        logger.info(
            "Ollama ready at %s with model %s",
            self._base_url,
            self._model,
        )

    async def complete(self, prompt: str, *, system: str | None = None) -> LLMResponse:
        """Generate a completion using Ollama /api/chat."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": self._max_tokens},
        }

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                response = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(
                f"Lost connection to Ollama at {self._base_url} during inference. "
                "Ensure Ollama is running: open -a Ollama"
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else str(exc)
            raise OllamaUnavailableError(
                f"Ollama inference failed (HTTP {exc.response.status_code}): {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(f"Ollama request failed: {exc}") from exc

        elapsed_ms = (time.monotonic() - started) * 1000
        message = data.get("message") or {}
        content = message.get("content", "").strip()
        if not content:
            raise OllamaUnavailableError(
                f"Ollama returned an empty response for model '{self._model}'."
            )

        tokens_in = int(data.get("prompt_eval_count") or _estimate_tokens(prompt, system))
        tokens_out = int(data.get("eval_count") or _estimate_tokens(content))

        logger.debug(
            "Ollama completion model=%s tokens_in=%s tokens_out=%s latency_ms=%.1f",
            self._model,
            tokens_in,
            tokens_out,
            elapsed_ms,
        )

        return LLMResponse(
            content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=self._model,
            latency_ms=elapsed_ms,
            prompt=prompt,
            system=system,
        )


def _estimate_tokens(*texts: str | None) -> int:
    """Rough token estimate when Ollama omits eval counts (~4 chars/token)."""
    combined = " ".join(t for t in texts if t)
    return max(1, len(combined) // 4)
