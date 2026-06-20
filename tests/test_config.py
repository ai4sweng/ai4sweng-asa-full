"""Tests for shared.config — settings loading and validators."""

import os

import pytest


class TestSettings:
    def test_default_values(self):
        from shared.config import Settings

        assert Settings.model_fields["nats_host"].default == "localhost"
        assert Settings.model_fields["nats_port"].default == 4222
        assert Settings.model_fields["jwt_algorithm"].default == "HS256"
        assert Settings.model_fields["llm_provider"].default == "ollama"
        assert Settings.model_fields["db_pool_size"].default == 10
        assert Settings.model_fields["db_max_overflow"].default == 20
        assert Settings.model_fields["openrouter_model"].default == "google/gemma-7b-it"
        assert Settings.model_fields["openrouter_api_key"].default == ""



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

    def test_jwt_secret_accepted_when_valid(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        from shared.config import Settings
        valid_secret = "a" * 32  # meets the 32-char minimum
        s = Settings(jwt_secret_key=valid_secret)
        assert s.jwt_secret_key == valid_secret

    def test_custom_pool_config(self):
        from shared.config import Settings
        s = Settings(db_pool_size=5, db_max_overflow=10)
        assert s.db_pool_size == 5
        assert s.db_max_overflow == 10

    def test_openai_and_anthropic_keys(self):
        from shared.config import Settings
        s = Settings(
            openai_api_key="sk-test",
            anthropic_api_key="sk-ant-test",
            openrouter_api_key="sk-or-test",
        )
        assert s.openai_api_key == "sk-test"
        assert s.anthropic_api_key == "sk-ant-test"
        assert s.openrouter_api_key == "sk-or-test"


