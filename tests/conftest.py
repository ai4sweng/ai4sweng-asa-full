"""Shared pytest fixtures and configuration."""

import pytest


# Mark all async tests with asyncio mode
def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as async")
