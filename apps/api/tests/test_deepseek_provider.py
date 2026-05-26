"""DeepSeek provider and Author routing tests."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agentforge.api.lifespan import _build_model_client
from agentforge.config import Settings, mask_api_key
from agentforge.models.client import ModelClientError, ModelMessage, TextBlock
from agentforge.models.deepseek_client import (
    DeepSeekModelClient,
    deepseek_configuration_status,
)
from agentforge.orchestrator.author_llm_authoring import (
    _client_for_purpose,
    _codegen_max_tokens_for_purpose,
    _repair_client_for_attempt,
)


def test_settings_deepseek_defaults() -> None:
    settings = Settings()
    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.deepseek_strong_model == "deepseek-v4-pro"
    assert settings.author_planning_model == "deepseek-v4-pro"
    assert settings.author_review_model == "deepseek-v4-pro"
    assert settings.author_codegen_model == "deepseek-v4-flash"
    assert settings.author_testgen_model == "deepseek-v4-flash"
    assert settings.author_repair_model == "deepseek-v4-flash"
    assert settings.author_enable_bank_reference_scaffold is False


def test_deepseek_client_requires_api_key() -> None:
    with pytest.raises(ModelClientError, match="DEEPSEEK_API_KEY"):
        DeepSeekModelClient(api_key="")


def test_build_model_client_deepseek_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-deepseek-key",
        deepseek_model="deepseek-v4-flash",
    )
    client = _build_model_client(settings)
    assert isinstance(client, DeepSeekModelClient)
    assert client.model == "deepseek-v4-flash"
    assert client.metadata["provider"] == "deepseek"


def test_build_model_client_deepseek_missing_key_raises() -> None:
    settings = Settings(llm_provider="deepseek", deepseek_api_key="")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        _build_model_client(settings)


def test_client_for_purpose_routes_deepseek_models() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-deepseek-key",
        author_planning_model="deepseek-v4-pro",
        author_codegen_model="deepseek-v4-flash",
        deepseek_temperature=0.2,
    )
    base = DeepSeekModelClient(api_key="test-deepseek-key")

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

    assert isinstance(planning, DeepSeekModelClient)
    assert isinstance(codegen, DeepSeekModelClient)
    assert planning.model == "deepseek-v4-pro"
    assert codegen.model == "deepseek-v4-flash"
    assert codegen._temperature == 0.2


def test_repair_client_for_attempt_uses_configured_deepseek_repair_model_on_retry() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-deepseek-key",
        author_repair_model="deepseek-v4-flash",
    )
    base = DeepSeekModelClient(api_key="test-deepseek-key")

    first = _repair_client_for_attempt(
        model_client=base,
        settings=settings,
        attempt=1,
    )
    second = _repair_client_for_attempt(
        model_client=base,
        settings=settings,
        attempt=2,
    )

    assert isinstance(first, DeepSeekModelClient)
    assert first.model == "deepseek-v4-flash"
    assert isinstance(second, DeepSeekModelClient)
    assert second.model == "deepseek-v4-flash"


def test_repair_client_for_attempt_uses_configured_deepseek_repair_model_for_pytest_failures() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-deepseek-key",
        author_repair_model="deepseek-v4-flash",
    )
    base = DeepSeekModelClient(api_key="test-deepseek-key")

    repair = _repair_client_for_attempt(
        model_client=base,
        settings=settings,
        attempt=1,
        failure_kind="pytest",
    )

    assert isinstance(repair, DeepSeekModelClient)
    assert repair.model == "deepseek-v4-flash"


def test_codegen_max_tokens_omitted_for_all_deepseek_authoring_stages() -> None:
    settings = Settings(llm_provider="deepseek", deepseek_api_key="test-deepseek-key")
    for purpose in (
        "contract_planning",
        "contract_review",
        "contract_schema_repair",
        "json_repair",
        "test_generation",
        "code_generation",
        "safety_repair",
    ):
        assert _codegen_max_tokens_for_purpose(purpose, settings) is None


@pytest.mark.asyncio
async def test_deepseek_complete_omits_max_tokens_when_uncapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "id": "chatcmpl-test",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            del args

        async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> FakeResponse:
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = DeepSeekModelClient(api_key="test-deepseek-key")
    await client.complete(
        system_prompt="system",
        messages=[ModelMessage(role="user", content=[TextBlock(text="hi")])],
        tools=[],
    )

    assert "max_tokens" not in captured["json"]


@pytest.mark.asyncio
async def test_deepseek_complete_parses_openai_compatible_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "id": "chatcmpl-test",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok": true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            del args

        async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> FakeResponse:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = DeepSeekModelClient(
        api_key="test-deepseek-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )
    response = await client.complete(
        system_prompt="Return JSON only.",
        messages=[ModelMessage(role="user", content=[TextBlock(text='{"task":"plan"}')])],
        tools=[],
        max_tokens=512,
    )

    assert response.text == '{"ok": true}'
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 8
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert captured["headers"]["Authorization"] == "Bearer test-deepseek-key"


def test_deepseek_configuration_status_masks_secrets() -> None:
    ok, checks = deepseek_configuration_status(
        api_key="sk-test-key-value",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        strong_model="deepseek-v4-pro",
        stage_models={
            "planning": "deepseek-v4-pro",
            "review": "deepseek-v4-pro",
            "codegen": "deepseek-v4-flash",
            "testgen": "deepseek-v4-flash",
            "repair": "deepseek-v4-flash",
        },
    )
    assert ok is True
    assert checks["deepseek_api_key"] == "configured"
    assert "sk-test-key-value" not in json.dumps(checks)
    assert checks["local_ollama"] == "disabled — DeepSeek provider selected"
    assert checks["author_planning_model"] == "deepseek-v4-pro"


def test_mask_api_key_never_returns_full_secret() -> None:
    masked = mask_api_key("sk-a80b99add90445779d8b252d21b5dafd")
    assert masked != "sk-a80b99add90445779d8b252d21b5dafd"
    assert "..." in masked


@pytest.mark.asyncio
async def test_ready_does_not_require_ollama_when_deepseek_configured(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_if_called(**kwargs: Any) -> tuple[bool, str]:
        del kwargs
        raise AssertionError("Ollama readiness should not run in DeepSeek mode")

    monkeypatch.setattr(
        "agentforge.api.routers.health.check_ollama_ready",
        _fail_if_called,
    )

    response = app_client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    checks = body["checks"]
    assert checks["llm_provider"] == "DeepSeek API"
    assert checks["deepseek_api_key"] == "configured"
    assert "sk-test-key-value" not in json.dumps(checks)
    assert "ollama" not in checks
