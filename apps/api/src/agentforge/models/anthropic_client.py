"""Real :class:`ModelClient` backed by ``anthropic.AsyncAnthropic``.

INV-1 + INV-8: the Anthropic SDK's response objects never cross this
module boundary. Inside this file we accept and produce SDK shapes;
everything that escapes is one of our own typed dataclasses
(:class:`ModelResponse`, :class:`TokenUsage`, the :class:`ContentBlock`
union). The rest of the backend works with our types and is free of
vendor concerns.

Prompt caching follows the guidance in ``shared/prompt-caching.md``:
we mark the last (only) system text block with
``cache_control: {"type": "ephemeral"}``. Because Anthropic renders
``tools → system → messages``, that single breakpoint caches both the
tool definitions and the system prompt; subsequent turns reuse the
prefix as long as the tool list, model, and system prompt are byte-
identical. The agent loop's bounded message accumulation keeps the
prefix stable across turns (INV-12 caps the run so the cache stays
warm for the whole session).

Retries: ``AsyncAnthropic`` already retries 408/409/429/5xx with
exponential backoff. We pass ``max_retries`` through and translate
terminal failures into :class:`ModelClientError` at the boundary
(INV-8 forbids raw SDK exceptions escaping). No hand-rolled retry
layer — duplicating the SDK's backoff would invite divergence.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from anthropic import AsyncAnthropic

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

_logger = logging.getLogger("agentforge.models.anthropic_client")

# Stop reasons our :class:`ModelResponse` literal recognises. The
# Anthropic API can return values outside this set (e.g. ``"refusal"``
# on Claude 4.x); we log + map to ``"end_turn"`` so the loop terminates
# rather than crash on Pydantic validation. Refinement to a richer
# stop-reason taxonomy is a BP10/11 concern.
_KNOWN_STOP_REASONS: frozenset[str] = frozenset(
    {"end_turn", "tool_use", "max_tokens", "stop_sequence"}
)


class AnthropicModelClient:
    """Async Claude Messages API client implementing :class:`ModelClient`.

    Defaults to ``claude-sonnet-4-6`` (Sonnet 4.5 is the predecessor;
    4.6 is the recommended migration target per Anthropic's migration
    guide). Override via :attr:`Settings.anthropic_model_primary` or
    construct directly with a different ``model``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise ModelClientError("anthropic api_key is empty")
        self._client = AsyncAnthropic(api_key=api_key, max_retries=max_retries)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "provider": "anthropic",
            "model": self._model,
            "base_url": "https://api.anthropic.com",
        }

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> ModelResponse:
        # Mark the (single) system text block with cache_control so the
        # tools + system prefix is cached. Tools render first, so the
        # marker on the last system block covers both.
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        message_params = [_message_to_anthropic(m) for m in messages]
        tools_param = tools or []

        try:
            sdk_response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=cast(Any, system_blocks),
                messages=cast(Any, message_params),
                tools=cast(Any, tools_param),
            )
        except Exception as exc:  # noqa: BLE001 — translate any SDK error at boundary
            # The Anthropic SDK raises ``anthropic.APIError`` subclasses
            # (RateLimitError, APIStatusError, etc.); we keep the
            # ``except`` broad so a future SDK exception type still
            # surfaces as ModelClientError rather than crashing the
            # loop (INV-8).
            _logger.exception("anthropic API call failed", extra={"model": self._model})
            raise ModelClientError(
                f"anthropic API error ({type(exc).__name__}): {exc}"
            ) from exc

        return _response_from_anthropic(sdk_response)


# ---------------------------------------------------------------------------
# Translation: our types → Anthropic SDK shape
# ---------------------------------------------------------------------------


def _message_to_anthropic(msg: ModelMessage) -> dict[str, Any]:
    """Convert a :class:`ModelMessage` to the Anthropic dict shape."""
    content: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            content.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": dict(block.input),
                }
            )
        elif isinstance(block, ToolResultBlock):
            content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
        else:  # pragma: no cover — discriminated union covers all cases
            raise ModelClientError(
                f"unknown content block type: {type(block).__name__}"
            )
    return {"role": msg.role, "content": content}


# ---------------------------------------------------------------------------
# Translation: Anthropic SDK response → our types
# ---------------------------------------------------------------------------


def _response_from_anthropic(response: Any) -> ModelResponse:
    """Convert an Anthropic ``Message`` SDK object to :class:`ModelResponse`."""
    blocks: list[ContentBlock] = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            blocks.append(TextBlock(text=block.text))
        elif block_type == "tool_use":
            raw_input = getattr(block, "input", None)
            input_dict: dict[str, Any] = dict(raw_input) if raw_input else {}
            blocks.append(
                ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=input_dict,
                )
            )
        else:
            # Thinking blocks, server-tool blocks, etc. are not supported
            # in BP5d. Drop quietly with a warning so the loop keeps
            # progressing; BP10/11 can extend the union if/when we wire
            # these features.
            _logger.warning(
                "unhandled content block type %r — dropping", block_type
            )

    raw_stop = response.stop_reason or "end_turn"
    if raw_stop not in _KNOWN_STOP_REASONS:
        _logger.warning(
            "unexpected stop_reason %r; mapping to 'end_turn'", raw_stop
        )
        stop_reason = "end_turn"
    else:
        stop_reason = raw_stop

    usage_obj = response.usage
    usage = TokenUsage(
        input_tokens=int(usage_obj.input_tokens or 0),
        output_tokens=int(usage_obj.output_tokens or 0),
        cache_read_input_tokens=int(
            getattr(usage_obj, "cache_read_input_tokens", 0) or 0
        ),
        cache_creation_input_tokens=int(
            getattr(usage_obj, "cache_creation_input_tokens", 0) or 0
        ),
    )

    return ModelResponse(
        id=response.id,
        content=blocks,
        stop_reason=stop_reason,
        usage=usage,
    )


__all__ = ["AnthropicModelClient"]
