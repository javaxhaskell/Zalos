"""Unit tests for :class:`AnthropicModelClient` (Build Prompt 5d).

The unit tests mock ``anthropic.AsyncAnthropic`` so they're deterministic
and don't hit the network. A separate ``test_anthropic_live_smoke`` is
marked ``@pytest.mark.live`` and skipped unless the user opts in with
``pytest -m live`` *and* a real ``ANTHROPIC_API_KEY`` is set.

Coverage:

  * ``_message_to_anthropic`` translates each :class:`ContentBlock`
    variant to the Anthropic dict shape.
  * ``_response_from_anthropic`` parses the SDK's response into our
    typed :class:`ModelResponse`.
  * ``cache_control={'type': 'ephemeral'}`` is set on the system block.
  * SDK exceptions are translated into :class:`ModelClientError`
    (INV-8 — no vendor exceptions leak).
  * Unexpected ``stop_reason`` values are mapped to ``end_turn`` with
    a log warning rather than failing Pydantic validation.
  * Token-usage fields ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens`` are propagated.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentforge.models import (
    AnthropicModelClient,
    ModelClientError,
    ModelMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agentforge.models.anthropic_client import (
    _message_to_anthropic,
    _response_from_anthropic,
)

# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------


def test_message_to_anthropic_translates_text_block() -> None:
    msg = ModelMessage(role="user", content=[TextBlock(text="hello")])
    assert _message_to_anthropic(msg) == {
        "role": "user",
        "content": [{"type": "text", "text": "hello"}],
    }


def test_message_to_anthropic_translates_tool_use_block() -> None:
    msg = ModelMessage(
        role="assistant",
        content=[
            ToolUseBlock(
                id="tu_1",
                name="list_workspace",
                input={"path": ".", "max_depth": 2},
            ),
        ],
    )
    out = _message_to_anthropic(msg)
    assert out["role"] == "assistant"
    assert out["content"] == [
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "list_workspace",
            "input": {"path": ".", "max_depth": 2},
        }
    ]


def test_message_to_anthropic_translates_tool_result_block() -> None:
    msg = ModelMessage(
        role="user",
        content=[
            ToolResultBlock(
                tool_use_id="tu_1", content='{"ok":true}', is_error=False
            ),
        ],
    )
    out = _message_to_anthropic(msg)
    assert out["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "tu_1",
            "content": '{"ok":true}',
            "is_error": False,
        }
    ]


def _fake_sdk_response(
    *,
    msg_id: str = "msg_test",
    text: str = "hi",
    tool_uses: list[dict] | None = None,
    stop_reason: str = "end_turn",
    usage: dict | None = None,
) -> SimpleNamespace:
    content = [SimpleNamespace(type="text", text=text)]
    for tu in tool_uses or []:
        content.append(
            SimpleNamespace(
                type="tool_use",
                id=tu["id"],
                name=tu["name"],
                input=tu["input"],
            )
        )
    usage_obj = SimpleNamespace(
        input_tokens=(usage or {}).get("input_tokens", 100),
        output_tokens=(usage or {}).get("output_tokens", 50),
        cache_read_input_tokens=(usage or {}).get("cache_read_input_tokens", 80),
        cache_creation_input_tokens=(usage or {}).get(
            "cache_creation_input_tokens", 20
        ),
    )
    return SimpleNamespace(
        id=msg_id, content=content, stop_reason=stop_reason, usage=usage_obj
    )


def test_response_from_anthropic_parses_text_and_tool_use() -> None:
    sdk_response = _fake_sdk_response(
        text="I'll inspect.",
        tool_uses=[{"id": "tu_42", "name": "inspect_file", "input": {"path": "x"}}],
        stop_reason="tool_use",
    )
    out = _response_from_anthropic(sdk_response)
    assert out.id == "msg_test"
    assert out.stop_reason == "tool_use"
    assert out.text == "I'll inspect."
    assert [u.name for u in out.tool_uses] == ["inspect_file"]
    assert out.tool_uses[0].input == {"path": "x"}


def test_response_from_anthropic_propagates_cache_usage() -> None:
    sdk_response = _fake_sdk_response(
        usage={
            "input_tokens": 200,
            "output_tokens": 80,
            "cache_read_input_tokens": 150,
            "cache_creation_input_tokens": 50,
        }
    )
    out = _response_from_anthropic(sdk_response)
    assert out.usage.input_tokens == 200
    assert out.usage.output_tokens == 80
    assert out.usage.cache_read_input_tokens == 150
    assert out.usage.cache_creation_input_tokens == 50


def test_response_from_anthropic_maps_refusal_stop_reason_to_end_turn() -> None:
    sdk_response = _fake_sdk_response(stop_reason="refusal")
    out = _response_from_anthropic(sdk_response)
    assert out.stop_reason == "end_turn"


def test_response_from_anthropic_drops_unknown_block_types() -> None:
    # Thinking + server-tool blocks aren't in our union yet; they should
    # be skipped with a warning rather than crashing the parse.
    sdk_response = SimpleNamespace(
        id="msg_x",
        content=[
            SimpleNamespace(type="thinking", thinking="internal monologue"),
            SimpleNamespace(type="text", text="visible output"),
        ],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    out = _response_from_anthropic(sdk_response)
    assert len(out.content) == 1
    assert out.text == "visible output"


# ---------------------------------------------------------------------------
# Client integration (mocked SDK)
# ---------------------------------------------------------------------------


def test_client_construct_with_empty_api_key_raises() -> None:
    with pytest.raises(ModelClientError, match="empty"):
        AnthropicModelClient(api_key="")


async def test_client_marks_system_block_with_cache_control() -> None:
    mock_create = AsyncMock(return_value=_fake_sdk_response())
    with patch("agentforge.models.anthropic_client.AsyncAnthropic", create=True) as fake_anthropic:
        fake_instance = fake_anthropic.return_value
        fake_instance.messages.create = mock_create
        client = AnthropicModelClient(api_key="sk-ant-test", model="claude-sonnet-4-6")
        await client.complete(
            system_prompt="be helpful",
            messages=[ModelMessage(role="user", content=[TextBlock(text="hi")])],
            tools=[],
        )
    kwargs = mock_create.call_args.kwargs
    assert kwargs["system"] == [
        {
            "type": "text",
            "text": "be helpful",
            "cache_control": {"type": "ephemeral"},
        }
    ]


async def test_client_passes_model_and_max_tokens() -> None:
    mock_create = AsyncMock(return_value=_fake_sdk_response())
    with patch("agentforge.models.anthropic_client.AsyncAnthropic", create=True) as fake_anthropic:
        fake_anthropic.return_value.messages.create = mock_create
        client = AnthropicModelClient(api_key="sk-ant-test", model="claude-opus-4-7")
        await client.complete(
            system_prompt="s",
            messages=[ModelMessage(role="user", content=[TextBlock(text="hi")])],
            tools=[{"name": "t", "description": "t", "input_schema": {}}],
            max_tokens=2048,
        )
    kwargs = mock_create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["max_tokens"] == 2048
    assert kwargs["tools"] == [{"name": "t", "description": "t", "input_schema": {}}]


async def test_client_translates_sdk_exception_to_model_client_error() -> None:
    mock_create = AsyncMock(side_effect=RuntimeError("API down"))
    with patch("agentforge.models.anthropic_client.AsyncAnthropic", create=True) as fake_anthropic:
        fake_anthropic.return_value.messages.create = mock_create
        client = AnthropicModelClient(api_key="sk-ant-test")
        with pytest.raises(ModelClientError, match="anthropic API error"):
            await client.complete(
                system_prompt="s", messages=[], tools=[]
            )


async def test_client_returns_typed_model_response_from_sdk_object() -> None:
    sdk_response = _fake_sdk_response(
        text="all good", tool_uses=[{"id": "tu_1", "name": "list_workspace", "input": {}}]
    )
    mock_create = AsyncMock(return_value=sdk_response)
    with patch("agentforge.models.anthropic_client.AsyncAnthropic", create=True) as fake_anthropic:
        fake_anthropic.return_value.messages.create = mock_create
        client = AnthropicModelClient(api_key="sk-ant-test")
        out = await client.complete(
            system_prompt="s",
            messages=[ModelMessage(role="user", content=[TextBlock(text="hi")])],
            tools=[],
        )
    assert out.id == "msg_test"
    assert [b.text for b in out.content if hasattr(b, "text")] == ["all good"]
    assert [u.name for u in out.tool_uses] == ["list_workspace"]


# ---------------------------------------------------------------------------
# Live-only smoke test
# ---------------------------------------------------------------------------


_LIVE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_LIVE_READY = _LIVE_KEY.startswith("sk-ant-")


@pytest.mark.live
@pytest.mark.skipif(
    not _LIVE_READY,
    reason=(
        "live test requires a real ANTHROPIC_API_KEY (starts with 'sk-ant-') "
        "and opt-in via `pytest -m live`."
    ),
)
async def test_anthropic_live_smoke() -> None:
    """Round-trip one trivial request against the real API.

    Purpose: verify the translation layer end-to-end on a live model
    once before submission. Not part of the CI suite — guarded by the
    ``live`` marker and the real-key check above.
    """
    client = AnthropicModelClient(
        api_key=_LIVE_KEY, model="claude-haiku-4-5", max_retries=1
    )
    out = await client.complete(
        system_prompt="You answer with exactly the single word the user asks for.",
        messages=[
            ModelMessage(
                role="user",
                content=[TextBlock(text="Say the word 'pong' and nothing else.")],
            )
        ],
        tools=[],
        max_tokens=32,
    )
    assert out.stop_reason == "end_turn"
    assert "pong" in out.text.lower()
    assert out.usage.input_tokens > 0
    assert out.usage.output_tokens > 0
