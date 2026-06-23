"""Shared pytest fixtures and configuration."""

import pytest


# Mark all async tests with asyncio mode
def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as async")


@pytest.fixture(autouse=True)
def _disable_hosted_planner(monkeypatch):
    """Force the orchestrator planner onto the local/LM-Engine path in tests.

    Production may set ``PLANNER_PROVIDER=anthropic`` (planning via Claude Haiku),
    but unit tests that construct a real ``LmEngineClient`` mock ``_client.post``
    and expect the HTTP path — without this they'd make live API calls. Tests
    that want to exercise the hosted planner can override ``_planner_provider``.
    """

    async def _no_hosted_planner(self):
        return None

    monkeypatch.setattr(
        "apps.orchestrator.src.services.lm_client.LmEngineClient._planner_provider",
        _no_hosted_planner,
        raising=False,
    )
