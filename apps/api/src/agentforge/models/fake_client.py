"""Deterministic :class:`ModelClient` for tests.

The fake client plays back a pre-scripted list of :class:`ModelResponse`
in order. Each call to :meth:`complete` advances the script by one;
exhaustion raises :class:`ModelClientError` so a misbuilt test fails
loudly rather than hanging on a model call that was never set up.

The script is a list of ``ModelResponse`` objects rather than a callable
because the BP5a integration tests are intentionally linear — every
step is pre-known. BP5b/c may grow this into a small DSL once the
orchestrator drives more interesting branching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentforge.models.client import (
    ModelClientError,
    ModelMessage,
    ModelResponse,
)


@dataclass
class FakeModelClient:
    """Plays back ``script`` one ``ModelResponse`` per :meth:`complete` call."""

    script: list[ModelResponse]
    calls: list[dict[str, Any]] = field(default_factory=list)
    """Record of every call's kwargs — tests assert against this."""

    _cursor: int = 0

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> ModelResponse:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [m.model_dump(mode="json") for m in messages],
                "tools": tools,
                "max_tokens": max_tokens,
            }
        )
        if self._cursor >= len(self.script):
            raise ModelClientError(
                f"FakeModelClient script exhausted after {self._cursor} call(s); "
                f"test asked for response #{self._cursor + 1}"
            )
        response = self.script[self._cursor]
        self._cursor += 1
        return response

    @property
    def call_count(self) -> int:
        return self._cursor

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "provider": "fake",
            "model": "fake-scripted",
            "base_url": "",
        }
