"""DeepSeek API :class:`ModelClient` via the OpenAI-compatible chat completions API.

Uses ``POST {base_url}/chat/completions`` with Bearer auth. Responses are
translated into AgentForge's :class:`ModelResponse` so the agent loop stays
provider-agnostic (INV-8).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal, cast

import httpx

from agentforge.models.client import (
    ContentBlock,
    ModelClientError,
    ModelMessage,
    ModelResponse,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)

_logger = logging.getLogger("agentforge.models.deepseek_client")

_KNOWN_STOP_REASONS: frozenset[str] = frozenset(
    {"end_turn", "tool_use", "max_tokens", "stop_sequence"}
)

_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}


class DeepSeekModelClient:
    """Async DeepSeek chat client implementing :class:`ModelClient`."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
        timeout_seconds: float = 300.0,
        temperature: float | None = None,
        max_retries: int = 2,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ModelClientError(
                "DEEPSEEK_API_KEY is missing. Set it in .env when LLM_PROVIDER=deepseek."
            )
        base = base_url.rstrip("/")
        if not base:
            raise ModelClientError("deepseek base_url is empty")
        self._api_key = key
        self._base_url = base
        self._model = model
        self._timeout = timeout_seconds
        self._temperature = temperature
        self._max_retries = max(0, max_retries)

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "provider": "deepseek",
            "model": self._model,
            "base_url": self._base_url,
        }

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _messages_to_openai(system_prompt, messages),
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        if tools:
            payload["tools"] = _tools_to_openai(tools)

        url = _chat_completions_url(self._base_url)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_error: ModelClientError | None = None
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
            except httpx.ConnectError as exc:
                last_error = ModelClientError(
                    f"cannot reach DeepSeek API at {self._base_url}: {exc}"
                )
            except httpx.TimeoutException as exc:
                last_error = ModelClientError(
                    f"DeepSeek request timed out after {self._timeout}s ({exc})"
                )
            except httpx.HTTPError as exc:
                last_error = ModelClientError(f"DeepSeek HTTP error: {exc}")
            else:
                try:
                    return _response_from_openai(response)
                except ModelClientError as exc:
                    last_error = exc
                    if response.status_code != 429 and response.status_code < 500:
                        raise

            if attempt + 1 < attempts and last_error is not None:
                _logger.warning(
                    "deepseek request failed; retrying",
                    extra={"attempt": attempt + 1, "model": self._model},
                )
                continue

        assert last_error is not None
        raise last_error


def deepseek_configuration_status(
    *,
    api_key: str,
    base_url: str,
    model: str,
    strong_model: str,
    stage_models: dict[str, str] | None = None,
) -> tuple[bool, dict[str, str]]:
    """Return ``(ok, checks)`` for readiness probes without exposing secrets."""
    stage_models = stage_models or {}
    checks: dict[str, str] = {
        "llm_provider": "DeepSeek API",
        "deepseek_api_key": "configured" if api_key.strip() else "missing — set DEEPSEEK_API_KEY",
        "deepseek_base_url": base_url.rstrip("/") or "(empty)",
        "deepseek_model": model,
        "deepseek_strong_model": strong_model,
        "author_planning_model": stage_models.get("planning", ""),
        "author_review_model": stage_models.get("review", ""),
        "author_codegen_model": stage_models.get("codegen", ""),
        "author_testgen_model": stage_models.get("testgen", ""),
        "author_repair_model": stage_models.get("repair", ""),
        "local_ollama": "disabled — DeepSeek provider selected",
    }
    ok = bool(api_key.strip()) and bool(base_url.strip()) and bool(model.strip())
    return ok, checks


def _chat_completions_url(base_url: str) -> str:
    """Build the DeepSeek chat-completions URL from either root or /v1 base URLs."""
    base = base_url.rstrip("/")
    suffix = "/chat/completions"
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def _tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for tool in tools
    ]


def _messages_to_openai(
    system_prompt: str,
    messages: list[ModelMessage],
) -> list[dict[str, Any]]:
    openai_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    tool_id_to_name: dict[str, str] = {}

    for msg in messages:
        if msg.role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_id_to_name[block.id] = block.name
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(dict(block.input)),
                            },
                        }
                    )
            assistant: dict[str, Any] = {"role": "assistant"}
            if text_parts:
                assistant["content"] = "".join(text_parts)
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            openai_messages.append(assistant)
            continue

        text_parts: list[str] = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolResultBlock):
                tool_name = tool_id_to_name.get(block.tool_use_id, block.tool_use_id)
                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.tool_use_id,
                        "name": tool_name,
                        "content": block.content,
                    }
                )
        if text_parts:
            openai_messages.append({"role": "user", "content": "".join(text_parts)})

    return openai_messages


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _response_from_openai(response: httpx.Response) -> ModelResponse:
    if response.status_code == 401:
        raise ModelClientError(
            "DeepSeek API rejected the API key (HTTP 401). Check DEEPSEEK_API_KEY."
        )
    if response.status_code == 429:
        raise ModelClientError(
            "DeepSeek API rate limit reached (HTTP 429). Retry later or reduce call volume."
        )
    if response.status_code >= 400:
        detail = response.text[:500]
        raise ModelClientError(f"DeepSeek API returned HTTP {response.status_code}: {detail}")

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ModelClientError("DeepSeek returned non-JSON response") from exc

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelClientError("DeepSeek response missing choices")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise ModelClientError("DeepSeek response choice is malformed")

    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelClientError("DeepSeek response missing message object")

    blocks: list[ContentBlock] = []
    content_text = message.get("content")
    if isinstance(content_text, str) and content_text:
        blocks.append(TextBlock(text=content_text))

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                continue
            tool_id = str(call.get("id") or f"tu_{uuid.uuid4().hex[:12]}")
            blocks.append(
                ToolUseBlock(
                    id=tool_id,
                    name=name,
                    input=_parse_tool_arguments(fn.get("arguments")),
                )
            )

    finish_reason = choice.get("finish_reason")
    raw_stop = "tool_use" if any(isinstance(b, ToolUseBlock) for b in blocks) else "end_turn"
    if isinstance(finish_reason, str):
        raw_stop = _FINISH_REASON_MAP.get(finish_reason, raw_stop)
    if raw_stop not in _KNOWN_STOP_REASONS:
        raw_stop = "end_turn"
    stop_reason = cast(Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"], raw_stop)

    usage_payload = data.get("usage")
    usage = TokenUsage()
    if isinstance(usage_payload, dict):
        usage = TokenUsage(
            input_tokens=int(usage_payload.get("prompt_tokens") or 0),
            output_tokens=int(usage_payload.get("completion_tokens") or 0),
        )

    msg_id = str(data.get("id") or uuid.uuid4())
    return ModelResponse(
        id=msg_id,
        content=blocks,
        stop_reason=stop_reason,
        usage=usage,
    )


__all__ = ["DeepSeekModelClient", "deepseek_configuration_status"]
