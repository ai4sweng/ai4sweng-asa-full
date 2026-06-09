"""Tests for shared.config — settings loading and validators."""

import os

import pytest


class TestSettings:
    def test_default_values(self):
        from shared.config import Settings
        s = Settings()
        assert s.nats_url == "nats://localhost:4222"
        assert s.jwt_algorithm == "HS256"
        assert s.llm_provider == "ollama"
        assert s.db_pool_size == 10
        assert s.db_max_overflow == 20

    def test_target_repo_path_normalizes_empty(self):
        from shared.config import Settings
        s = Settings(target_repo_path="  ")
        assert s.target_repo_path == "examples/buggy_fastapi_repo"

    def test_target_repo_path_normalizes_none(self):
        from shared.config import Settings
        s = Settings(target_repo_path=None)
        assert s.target_repo_path == "examples/buggy_fastapi_repo"

    def test_jwt_secret_rejected_in_production(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        from shared.config import Settings
        with pytest.raises(Exception, match="JWT_SECRET_KEY"):
            Settings(jwt_secret_key="change-me-in-production")

    def test_jwt_secret_accepted_in_dev(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        from shared.config import Settings
        s = Settings(jwt_secret_key="change-me-in-production")
        assert s.jwt_secret_key == "change-me-in-production"

    def test_custom_pool_config(self):
        from shared.config import Settings
        s = Settings(db_pool_size=5, db_max_overflow=10)
        assert s.db_pool_size == 5
        assert s.db_max_overflow == 10

    def test_openai_and_anthropic_keys(self):
        from shared.config import Settings
        s = Settings(openai_api_key="sk-test", anthropic_api_key="sk-ant-test")
        assert s.openai_api_key == "sk-test"
        assert s.anthropic_api_key == "sk-ant-test"
