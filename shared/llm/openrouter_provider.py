"""OpenRouter provider via litellm — supports any model on the openrouter/ prefix."""

import time

from shared.llm.base import BaseLLMProvider, LLMResponse


class OpenRouterProvider(BaseLLMProvider):
    def __init__(self, model: str = "google/gemma-7b-it", api_key: str = "") -> None:
        self._model = model
        self._api_key = api_key

    @property
    def provider_name(self) -> str:
        return "openrouter"

    async def complete(self, prompt: str, *, system: str | None = None) -> LLMResponse:
        import litellm

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        started = time.monotonic()
        resp = await litellm.acompletion(
            model=f"openrouter/{self._model}",
            messages=messages,
            api_key=self._api_key or None,
        )
        latency_ms = (time.monotonic() - started) * 1000
        usage = resp.usage or {}
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            tokens_in=getattr(usage, "prompt_tokens", 0),
            tokens_out=getattr(usage, "completion_tokens", 0),
            model=self._model,
            latency_ms=latency_ms,
            prompt=prompt,
            system=system,
        )
