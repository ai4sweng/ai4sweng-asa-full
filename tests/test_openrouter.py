"""Tests for the OpenRouter provider and its registration in the LLM factory."""

import pytest
from unittest.mock import AsyncMock, patch
from shared.config import Settings
from shared.llm.openrouter_provider import OpenRouterProvider
from shared.llm.factory import create_llm_provider
from shared.llm.base import LLMResponse


@pytest.mark.asyncio
async def test_openrouter_provider_complete():
    # Mock Response object mimicking litellm's acompletion return value
    mock_choice = AsyncMock()
    mock_choice.message.content = "Hello from OpenRouter!"
    mock_usage = AsyncMock()
    mock_usage.prompt_tokens = 10
    mock_usage.completion_tokens = 20

    mock_resp = AsyncMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage = mock_usage

    provider = OpenRouterProvider(model="google/gemma-7b-it", api_key="sk-or-test-key")

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_resp

        response = await provider.complete("Test prompt", system="System instruction")

        assert isinstance(response, LLMResponse)
        assert response.content == "Hello from OpenRouter!"
        assert response.tokens_in == 10
        assert response.tokens_out == 20
        assert response.model == "google/gemma-7b-it"
        
        mock_acompletion.assert_called_once_with(
            model="openrouter/google/gemma-7b-it",
            messages=[
                {"role": "system", "content": "System instruction"},
                {"role": "user", "content": "Test prompt"},
            ],
            api_key="sk-or-test-key",
        )


@pytest.mark.asyncio
async def test_factory_creates_openrouter_provider():
    settings = Settings(
        llm_provider="openrouter",
        openrouter_api_key="sk-or-test-key",
        openrouter_model="google/gemma-7b-it",
    )
    provider = await create_llm_provider(settings=settings)
    
    # Provider is wrapped in ObservedLLMProvider, so we check the inner provider
    inner = provider._inner if hasattr(provider, "_inner") else provider
    assert isinstance(inner, OpenRouterProvider)
    assert inner._model == "google/gemma-7b-it"
    assert inner._api_key == "sk-or-test-key"


@pytest.mark.asyncio
async def test_factory_raises_system_exit_on_missing_api_key():
    settings = Settings(
        llm_provider="openrouter",
        openrouter_api_key="",
    )
    with pytest.raises(SystemExit):
        await create_llm_provider(settings=settings)
