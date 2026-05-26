"""Tests for the deterministic :class:`FakeModelClient` (BP5a).

The fake client is a test fixture, not a production component — so the
contract that matters is:

  * Each :meth:`complete` call returns the next scripted response.
  * Exhausting the script raises :class:`ModelClientError` rather than
    hanging or wrapping silently.
  * Every call's kwargs are recorded so tests can assert on what the
    loop sent (system prompt content, tool surface, etc.).
"""

from __future__ import annotations

import pytest

from agentforge.models import (
    FakeModelClient,
    ModelClientError,
    ModelMessage,
    ModelResponse,
    TextBlock,
    ToolUseBlock,
)


def _stub_response(text: str = "ok", call_id: str = "msg_001") -> ModelResponse:
    return ModelResponse(
        id=call_id,
        content=[TextBlock(text=text)],
        stop_reason="end_turn",
    )


async def test_fake_client_returns_scripted_responses_in_order() -> None:
    a = _stub_response("first", "msg_a")
    b = _stub_response("second", "msg_b")
    client = FakeModelClient(script=[a, b])

    first = await client.complete(system_prompt="s", messages=[], tools=[])
    second = await client.complete(system_prompt="s", messages=[], tools=[])

    assert first.id == "msg_a"
    assert first.text == "first"
    assert second.id == "msg_b"
    assert client.call_count == 2


async def test_fake_client_records_every_call() -> None:
    client = FakeModelClient(script=[_stub_response()])
    tools = [{"name": "list_workspace", "description": "...", "input_schema": {}}]
    msg = ModelMessage(role="user", content=[TextBlock(text="hi")])

    await client.complete(
        system_prompt="be helpful",
        messages=[msg],
        tools=tools,
        max_tokens=2048,
    )

    assert len(client.calls) == 1
    recorded = client.calls[0]
    assert recorded["system_prompt"] == "be helpful"
    assert recorded["tools"] == tools
    assert recorded["max_tokens"] == 2048
    assert recorded["messages"][0]["role"] == "user"


async def test_fake_client_raises_on_exhaustion() -> None:
    client = FakeModelClient(script=[_stub_response()])
    await client.complete(system_prompt="s", messages=[], tools=[])

    with pytest.raises(ModelClientError, match="exhausted"):
        await client.complete(system_prompt="s", messages=[], tools=[])


def test_model_response_extracts_tool_uses_and_text() -> None:
    resp = ModelResponse(
        id="msg_x",
        content=[
            TextBlock(text="I'll inspect the file."),
            ToolUseBlock(id="tu_1", name="inspect_file", input={"path": "uploads/a.csv"}),
            TextBlock(text=" Then list."),
            ToolUseBlock(id="tu_2", name="list_workspace", input={"path": "."}),
        ],
        stop_reason="tool_use",
    )
    assert resp.text == "I'll inspect the file. Then list."
    assert [u.name for u in resp.tool_uses] == ["inspect_file", "list_workspace"]
