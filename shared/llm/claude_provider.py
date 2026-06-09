"""Anthropic Claude provider via litellm — supports claude-opus-4-8, claude-sonnet-4-6, etc."""

import time

from shared.llm.base import BaseLLMProvider, LLMResponse


class ClaudeProvider(BaseLLMProvider):
    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str = "") -> None:
        self._model = model
        self._api_key = api_key

    @property
    def provider_name(self) -> str:
        return "claude"

    async def complete(self, prompt: str, *, system: str | None = None) -> LLMResponse:
        import litellm

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        started = time.monotonic()
        resp = await litellm.acompletion(
            model=f"anthropic/{self._model}",
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
