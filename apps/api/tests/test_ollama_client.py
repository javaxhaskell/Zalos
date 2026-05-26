"""Unit tests for :class:`OllamaModelClient` (mocked httpx, no network)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentforge.models import (
    ModelClientError,
    ModelMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agentforge.models.ollama_client import (
    OllamaModelClient,
    _messages_to_ollama,
    _response_from_ollama,
    _tools_to_ollama,
    check_ollama_ready,
)


def test_tools_to_ollama_maps_input_schema_to_parameters() -> None:
    out = _tools_to_ollama(
        [
            {
                "name": "list_workspace",
                "description": "List files",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        ]
    )
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "list_workspace",
                "description": "List files",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        }
    ]


def test_messages_to_ollama_includes_system_and_tool_results() -> None:
    msgs = [
        ModelMessage(role="user", content=[TextBlock(text="go")]),
        ModelMessage(
            role="assistant",
            content=[
                ToolUseBlock(id="tu_1", name="inspect_file", input={"path": "x"}),
            ],
        ),
        ModelMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="tu_1", content='{"ok":true}'),
            ],
        ),
    ]
    out = _messages_to_ollama("sys", msgs)
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[1] == {"role": "user", "content": "go"}
    assert out[2]["role"] == "assistant"
    assert out[2]["tool_calls"][0]["function"]["name"] == "inspect_file"
    assert out[3] == {
        "role": "tool",
        "tool_name": "inspect_file",
        "content": '{"ok":true}',
    }


def test_response_from_ollama_parses_text_and_tool_calls() -> None:
    out = _response_from_ollama(
        {
            "created_at": "2026-05-22T12:00:00Z",
            "prompt_eval_count": 10,
            "eval_count": 5,
            "message": {
                "role": "assistant",
                "content": "I'll inspect.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "inspect_file",
                            "arguments": {"path": "uploads/x.csv"},
                        }
                    }
                ],
            },
        }
    )
    assert out.stop_reason == "tool_use"
    assert out.text == "I'll inspect."
    assert len(out.tool_uses) == 1
    assert out.tool_uses[0].name == "inspect_file"
    assert out.tool_uses[0].input == {"path": "uploads/x.csv"}
    assert out.usage.input_tokens == 10
    assert out.usage.output_tokens == 5


async def test_client_connect_error_is_model_client_error() -> None:
    client = OllamaModelClient(base_url="http://127.0.0.1:59999", model="qwen2.5:7b")
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("refused")
        mock_client_cls.return_value = mock_client
        with pytest.raises(ModelClientError, match="cannot reach Ollama"):
            await client.complete(
                system_prompt="s",
                messages=[ModelMessage(role="user", content=[TextBlock(text="hi")])],
                tools=[],
            )


async def test_client_passes_num_ctx_in_options() -> None:
    client = OllamaModelClient(model="qwen2.5:7b", num_ctx=8192)
    fake_response = httpx.Response(
        200,
        json={
            "message": {"role": "assistant", "content": "ok"},
            "prompt_eval_count": 1,
            "eval_count": 1,
        },
        request=httpx.Request("POST", "http://x/api/chat"),
    )
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = fake_response
        mock_client_cls.return_value = mock_client
        await client.complete(system_prompt="s", messages=[], tools=[], max_tokens=512)
        payload = mock_client.post.call_args.kwargs["json"]
    assert payload["options"]["num_ctx"] == 8192
    assert payload["options"]["num_predict"] == 512


async def test_client_404_suggests_pull() -> None:
    client = OllamaModelClient(model="missing:7b")
    fake_response = httpx.Response(404, request=httpx.Request("POST", "http://x/api/chat"))
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = fake_response
        mock_client_cls.return_value = mock_client
        with pytest.raises(ModelClientError, match="ollama pull"):
            await client.complete(system_prompt="s", messages=[], tools=[])


async def test_check_ollama_ready_when_model_missing() -> None:
    tags_json = {"models": [{"name": "llama3.2:latest"}]}
    tags_resp = httpx.Response(200, json=tags_json)
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = tags_resp
        mock_client_cls.return_value = mock_client
        ok, detail = await check_ollama_ready(
            base_url="http://localhost:11434", model="qwen2.5:7b"
        )
    assert ok is False
    assert "qwen2.5:7b" in detail
    assert "ollama pull" in detail


async def test_check_ollama_ready_when_model_present() -> None:
    tags_json = {"models": [{"name": "qwen2.5:7b"}]}
    tags_resp = httpx.Response(200, json=tags_json)
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = tags_resp
        mock_client_cls.return_value = mock_client
        ok, detail = await check_ollama_ready(
            base_url="http://localhost:11434", model="qwen2.5:7b"
        )
    assert ok is True
    assert "qwen2.5:7b" in detail
