"""Settings defaults for local Ollama model routing."""

from __future__ import annotations

import pytest

from agentforge.config import Settings, local_ollama_codegen_warning
from agentforge.models import DeepSeekModelClient, FakeModelClient, OllamaModelClient
from agentforge.orchestrator.author_llm_authoring import (
    _client_for_purpose,
    _codegen_max_tokens_for_purpose,
    _configured_timeout_seconds,
    _timeout_seconds_for_purpose,
)


def test_author_token_budget_defaults() -> None:
    settings = Settings()
    assert settings.author_max_repair_attempts == 1
    assert settings.author_small_workflow_row_threshold == 25
    assert settings.author_compact_contract_prompts is True


def test_ollama_defaults_split_planning_14b_and_codegen_7b() -> None:
    settings = Settings()
    assert settings.ollama_model == "qwen2.5-coder:14b"
    assert settings.ollama_planning_model == "qwen2.5-coder:14b"
    assert settings.ollama_codegen_model == "qwen2.5-coder:7b"


def test_ollama_planning_model_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_PLANNING_MODEL", "custom-planning-model:7b")
    settings = Settings()
    assert settings.ollama_planning_model == "custom-planning-model:7b"
    assert settings.ollama_codegen_model == "qwen2.5-coder:7b"


def test_ollama_global_timeout_default() -> None:
    settings = Settings()
    assert settings.ollama_timeout_seconds == 900.0
    assert "ollama_codegen_max_tokens" not in Settings.model_fields


def test_timeout_seconds_for_purpose_uses_global_timeout() -> None:
    settings = Settings(llm_provider="ollama", ollama_timeout_seconds=900)
    for purpose in (
        "contract_planning",
        "code_generation",
        "code_adaptation",
        "test_generation",
        "safety_repair",
    ):
        assert _timeout_seconds_for_purpose(purpose, settings) == 900.0


def test_client_for_purpose_applies_global_timeout_for_all_stages() -> None:
    settings = Settings(
        llm_provider="ollama",
        ollama_planning_model="planning-model:14b",
        ollama_codegen_model="codegen-model:14b",
        ollama_timeout_seconds=900,
    )
    base = OllamaModelClient(model="base-model:14b")

    for purpose in (
        "contract_planning",
        "code_generation",
        "test_generation",
        "safety_repair",
    ):
        client = _client_for_purpose(
            model_client=base,
            purpose=purpose,
            settings=settings,
        )
        assert isinstance(client, OllamaModelClient)
        assert client._timeout == 900.0


def test_codegen_max_tokens_omitted_for_deepseek() -> None:
    settings = Settings(llm_provider="deepseek")
    for purpose in (
        "contract_planning",
        "contract_schema_repair",
        "json_repair",
        "contract_review",
        "code_generation",
        "code_adaptation",
        "test_generation",
        "safety_repair",
    ):
        assert _codegen_max_tokens_for_purpose(purpose, settings) is None


def test_codegen_max_tokens_omitted_for_codegen_stages_on_ollama() -> None:
    settings = Settings(llm_provider="ollama")
    assert _codegen_max_tokens_for_purpose("contract_planning", settings) == 4096
    assert _codegen_max_tokens_for_purpose("contract_schema_repair", settings) == 4096
    assert _codegen_max_tokens_for_purpose("json_repair", settings) == 4096
    assert _codegen_max_tokens_for_purpose("contract_review", settings) == 4096
    assert _codegen_max_tokens_for_purpose("code_generation", settings) is None
    assert _codegen_max_tokens_for_purpose("code_adaptation", settings) is None
    assert _codegen_max_tokens_for_purpose("test_generation", settings) == 4096


def test_configured_timeout_seconds_uses_global_for_ollama() -> None:
    settings = Settings(llm_provider="ollama", ollama_timeout_seconds=900)
    client = OllamaModelClient(model="qwen2.5-coder:14b", timeout_seconds=900)
    assert _configured_timeout_seconds(client, settings, purpose="code_generation") == 900.0
    assert _configured_timeout_seconds(client, settings, purpose="test_generation") == 900.0


def test_client_for_purpose_routes_planning_and_codegen_models() -> None:
    settings = Settings(
        llm_provider="ollama",
        ollama_planning_model="planning-model:14b",
        ollama_codegen_model="codegen-model:14b",
        ollama_planning_num_ctx=8192,
        ollama_num_ctx=12288,
        ollama_codegen_temperature=0.2,
    )
    base = OllamaModelClient(model="base-model:14b")

    planning = _client_for_purpose(
        model_client=base,
        purpose="contract_planning",
        settings=settings,
    )
    codegen = _client_for_purpose(
        model_client=base,
        purpose="code_generation",
        settings=settings,
    )

    assert isinstance(planning, OllamaModelClient)
    assert isinstance(codegen, OllamaModelClient)
    assert planning.model == "planning-model:14b"
    assert codegen.model == "codegen-model:14b"
    assert codegen._temperature == 0.2


def test_ollama_codegen_model_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_CODEGEN_MODEL", "fast-codegen:7b")
    settings = Settings()
    assert settings.ollama_codegen_model == "fast-codegen:7b"


def test_local_ollama_codegen_warning_for_14b_codegen() -> None:
    settings = Settings(
        llm_provider="ollama",
        ollama_codegen_model="qwen2.5-coder:14b",
    )
    warning = local_ollama_codegen_warning(settings)
    assert warning is not None
    assert "qwen2.5-coder:7b" in warning


def test_local_ollama_codegen_warning_omitted_for_recommended_codegen() -> None:
    settings = Settings(
        llm_provider="ollama",
        ollama_codegen_model="qwen2.5-coder:7b",
    )
    assert local_ollama_codegen_warning(settings) is None


def test_client_for_purpose_leaves_non_routed_clients_unchanged() -> None:
    fake = FakeModelClient(script=[])
    settings = Settings(llm_provider="anthropic")
    routed = _client_for_purpose(
        model_client=fake,
        purpose="contract_planning",
        settings=settings,
    )
    assert routed is fake


def test_deepseek_default_provider() -> None:
    settings = Settings()
    assert settings.llm_provider == "deepseek"


def test_author_model_provider_alias_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("AUTHOR_MODEL_PROVIDER", "ollama")
    settings = Settings()
    assert settings.llm_provider == "ollama"


def test_deepseek_author_stage_model_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_PLANNING_MODEL", "planning-pro")
    monkeypatch.setenv("AUTHOR_REVIEW_MODEL", "review-pro")
    monkeypatch.setenv("AUTHOR_CODEGEN_MODEL", "codegen-flash")
    settings = Settings(llm_provider="deepseek", deepseek_api_key="test-deepseek-key")
    base = DeepSeekModelClient(api_key="test-deepseek-key")
    planning = _client_for_purpose(
        model_client=base,
        purpose="contract_planning",
        settings=settings,
    )
    review = _client_for_purpose(
        model_client=base,
        purpose="contract_review",
        settings=settings,
    )
    codegen = _client_for_purpose(
        model_client=base,
        purpose="code_generation",
        settings=settings,
    )
    assert isinstance(planning, DeepSeekModelClient)
    assert isinstance(review, DeepSeekModelClient)
    assert isinstance(codegen, DeepSeekModelClient)
    assert planning.model == "planning-pro"
    assert review.model == "review-pro"
    assert codegen.model == "codegen-flash"
