"""Model client surface.

Implementations:

  * :class:`FakeModelClient` — deterministic replay for tests/evals.
  * :class:`AnthropicModelClient` — Claude API (BP5d).
  * :class:`DeepSeekModelClient` — DeepSeek OpenAI-compatible API.
  * :class:`OllamaModelClient` — local Ollama HTTP API (optional).
"""

from __future__ import annotations

from agentforge.models.anthropic_client import AnthropicModelClient
from agentforge.models.client import (
    ContentBlock,
    ModelClient,
    ModelClientError,
    ModelMessage,
    ModelResponse,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from agentforge.models.deepseek_client import DeepSeekModelClient
from agentforge.models.fake_client import FakeModelClient
from agentforge.models.ollama_client import OllamaModelClient

__all__ = [
    "AnthropicModelClient",
    "ContentBlock",
    "DeepSeekModelClient",
    "FakeModelClient",
    "ModelClient",
    "OllamaModelClient",
    "ModelClientError",
    "ModelMessage",
    "ModelResponse",
    "TextBlock",
    "TokenUsage",
    "ToolResultBlock",
    "ToolUseBlock",
]
