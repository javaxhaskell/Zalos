"""Typed model-client contract.

The agent loop calls :class:`ModelClient.complete` once per step. The
protocol is small and pure-async because (a) all our concrete clients
will be async (anthropic SDK exposes ``AsyncAnthropic``), and (b) the
loop's per-step branch on ``ModelResponse.stop_reason`` is the same
regardless of provider.

The block types below mirror the Anthropic Messages API closely so the
real client (BP5d) translates 1:1, but they are deliberately our own
types — INV-8 forbids passing vendor SDK objects across module
boundaries.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from agentforge.schemas import StrictModel

# ---------------------------------------------------------------------------
# Content blocks (discriminated by ``type``)
# ---------------------------------------------------------------------------


class TextBlock(StrictModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(StrictModel):
    """The model proposing a tool call."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    """JSON-Schema-shaped input. Validated against the registry tool's
    Pydantic input schema before dispatch."""


class ToolResultBlock(StrictModel):
    """Observation we return to the model on the next turn."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    """The observation as a JSON string. The fake client compares this
    as opaque text; the real client will sometimes attach structured
    content arrays. For BP5a all results are JSON-encoded strings."""
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class ModelMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: list[ContentBlock]


class TokenUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Tokens that count against the per-session budget.

        Cache reads count at a discount in Anthropic pricing; the agent
        loop uses the *un-discounted* total for the budget check so a
        cache-friendly system prompt does not silently extend the
        budget. The real billed cost is computed separately by the
        pricing module in BP12.
        """
        return self.input_tokens + self.output_tokens


class ModelResponse(StrictModel):
    """One response from the model."""

    id: str
    """Provider-assigned message id (real client) or synthetic id (fake)."""
    content: list[ContentBlock] = Field(default_factory=list)
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"]
    usage: TokenUsage = Field(default_factory=TokenUsage)

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ModelClient(Protocol):
    """Provider-agnostic completion interface used by the agent loop.

    Implementations:

      * :class:`agentforge.models.fake_client.FakeModelClient` — scripted
        replay for tests/evals (BP5a).
      * :class:`agentforge.models.anthropic_client.AnthropicModelClient`
        — Claude API (BP5d).
      * :class:`agentforge.models.deepseek_client.DeepSeekModelClient`
        — DeepSeek OpenAI-compatible API.
      * :class:`agentforge.models.ollama_client.OllamaModelClient`
        — local Ollama HTTP API (optional).
    """

    @property
    def metadata(self) -> dict[str, str]:
        """Provider identity surfaced into every ``MODEL_CALLED`` event.

        Keys: ``provider`` (e.g. ``"ollama"``), ``model``, ``base_url``
        (empty string when not applicable, e.g. for the fake client).
        """
        ...

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> ModelResponse:
        ...


class ModelClientError(RuntimeError):
    """Raised when a model client cannot produce a response.

    Includes provider-side 4xx/5xx (real client) and script exhaustion
    (fake client). The loop converts this into a terminal
    ``failed_model`` session status via :class:`ErrorCode`.
    """
