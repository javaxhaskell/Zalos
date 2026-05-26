"""Local :class:`ModelClient` backed by the Ollama HTTP API.

Uses ``POST /api/chat`` with tool definitions in OpenAI-style shape.
Responses are translated into AgentForge's :class:`ModelResponse` so the
agent loop stays provider-agnostic (INV-8).

Health helpers (:func:`check_ollama_ready`) power ``GET /health/ready`` when
``LLM_PROVIDER=ollama``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
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

_logger = logging.getLogger("agentforge.models.ollama_client")

_KNOWN_STOP_REASONS: frozenset[str] = frozenset(
    {"end_turn", "tool_use", "max_tokens", "stop_sequence"}
)


class OllamaModelClient:
    """Async Ollama chat client implementing :class:`ModelClient`."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:14b",
        timeout_seconds: float = 300.0,
        num_ctx: int = 16384,
        temperature: float | None = None,
    ) -> None:
        base = base_url.rstrip("/")
        if not base:
            raise ModelClientError("ollama base_url is empty")
        self._base_url = base
        self._model = model
        self._timeout = timeout_seconds
        self._num_ctx = num_ctx
        self._temperature = temperature

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "provider": "ollama",
            "model": self._model,
            "base_url": self._base_url,
        }

    def request_options(self, *, max_tokens: int | None = 4096) -> dict[str, Any]:
        """Return the exact ``options`` object sent to Ollama for a request."""
        options: dict[str, Any] = {
            "num_ctx": self._num_ctx,
        }
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if self._temperature is not None:
            options["temperature"] = self._temperature
        return options

    async def complete(
        self,
        *,
        system_prompt: str,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        max_tokens: int | None = 4096,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _messages_to_ollama(system_prompt, messages),
            "stream": False,
            "options": self.request_options(max_tokens=max_tokens),
        }
        if tools:
            payload["tools"] = _tools_to_ollama(tools)

        url = f"{self._base_url}/api/chat"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)
        except httpx.ConnectError as exc:
            raise ModelClientError(
                f"cannot reach Ollama at {self._base_url}: is `ollama serve` running? ({exc})"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ModelClientError(
                f"Ollama request timed out after {self._timeout}s ({exc})"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelClientError(f"Ollama HTTP error: {exc}") from exc

        if response.status_code == 404:
            raise ModelClientError(
                f"Ollama model {self._model!r} not found. Pull it with: ollama pull {self._model}"
            )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise ModelClientError(f"Ollama API returned HTTP {response.status_code}: {detail}")

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ModelClientError("Ollama returned non-JSON response") from exc

        return _response_from_ollama(data)

    async def diagnose_stream(
        self,
        *,
        system_prompt: str,
        messages: list[ModelMessage],
        max_tokens: int | None = None,
        partial_output_path: Path | None = None,
    ) -> OllamaStreamDiagnostics:
        """Diagnostic-only streaming call — measures TTFT and Ollama eval stats."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _messages_to_ollama(system_prompt, messages),
            "stream": True,
            "options": self.request_options(max_tokens=max_tokens),
        }

        url = f"{self._base_url}/api/chat"
        started = time.perf_counter()
        first_token_at: float | None = None
        content_parts: list[str] = []
        char_timeline: list[dict[str, Any]] = []
        final_stats: dict[str, Any] = {}
        timed_out = False
        error_message: str | None = None
        if partial_output_path is not None:
            partial_output_path.parent.mkdir(parents=True, exist_ok=True)
            partial_output_path.write_text("", encoding="utf-8")

        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout) as client,
                client.stream("POST", url, json=payload) as response,
            ):
                if response.status_code == 404:
                    raise ModelClientError(
                        f"Ollama model {self._model!r} not found. "
                        f"Pull it with: ollama pull {self._model}"
                    )
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise ModelClientError(
                        f"Ollama API returned HTTP {response.status_code}: {body}"
                    )

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    message = chunk.get("message")
                    if isinstance(message, dict):
                        delta = message.get("content")
                        if isinstance(delta, str) and delta:
                            now = time.perf_counter()
                            if first_token_at is None:
                                first_token_at = now
                            content_parts.append(delta)
                            if partial_output_path is not None:
                                partial_output_path.write_text(
                                    "".join(content_parts),
                                    encoding="utf-8",
                                )
                            char_timeline.append(
                                {
                                    "elapsed_seconds": round(now - started, 3),
                                    "generated_chars": sum(len(p) for p in content_parts),
                                }
                            )

                    for key in (
                        "prompt_eval_count",
                        "prompt_eval_duration",
                        "eval_count",
                        "eval_duration",
                        "total_duration",
                    ):
                        if key in chunk and chunk[key] is not None:
                            final_stats[key] = chunk[key]

                    if chunk.get("done") is True:
                        break
        except httpx.ConnectError as exc:
            error_message = (
                f"cannot reach Ollama at {self._base_url}: is `ollama serve` running? ({exc})"
            )
        except httpx.TimeoutException as exc:
            timed_out = True
            error_message = f"Ollama request timed out after {self._timeout}s ({exc})"
        except httpx.HTTPError as exc:
            error_message = f"Ollama HTTP error: {exc}"
        except ModelClientError as exc:
            error_message = str(exc)

        finished = time.perf_counter()
        content = "".join(content_parts)
        time_to_first_token = (
            round(first_token_at - started, 3) if first_token_at is not None else None
        )
        total_elapsed = round(finished - started, 3)
        eval_count = int(final_stats.get("eval_count") or 0)
        eval_duration_ns = int(final_stats.get("eval_duration") or 0)
        tokens_per_second: float | None = None
        if eval_count > 0 and eval_duration_ns > 0:
            tokens_per_second = round(eval_count / (eval_duration_ns / 1e9), 2)

        return OllamaStreamDiagnostics(
            streamed=first_token_at is not None,
            silent_before_first_token=first_token_at is None
            and not timed_out
            and error_message is None,
            hung_before_first_token=first_token_at is None and timed_out,
            time_to_first_token_seconds=time_to_first_token,
            total_elapsed_seconds=total_elapsed,
            generated_chars=len(content),
            generated_text=content,
            char_timeline=char_timeline,
            ollama_stats=final_stats,
            tokens_per_second=tokens_per_second,
            timed_out=timed_out,
            error=error_message,
        )


async def check_ollama_ready(
    *,
    base_url: str,
    model: str,
    timeout_seconds: float = 5.0,
) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for readiness probes.

    Verifies the server responds and the configured model tag is present
    in ``GET /api/tags``.
    """
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            tags_resp = await client.get(f"{base}/api/tags")
    except httpx.ConnectError:
        return (
            False,
            f"cannot connect to Ollama at {base}; start with `ollama serve`",
        )
    except httpx.HTTPError as exc:
        return False, f"Ollama tags request failed: {exc}"

    if tags_resp.status_code >= 400:
        return False, f"Ollama /api/tags returned HTTP {tags_resp.status_code}"

    try:
        payload = tags_resp.json()
    except json.JSONDecodeError:
        return False, "Ollama /api/tags returned invalid JSON"

    names: list[str] = []
    for entry in payload.get("models", []):
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            names.append(entry["name"])

    if not names:
        return False, "Ollama has no models installed; run `ollama pull <model>`"

    model_base, _, model_tag = model.partition(":")
    tag = model_tag or "latest"
    matched = any(
        n == model or n.startswith(f"{model_base}:") and (tag == "latest" or n.endswith(f":{tag}"))
        for n in names
    )
    if not matched:
        available = ", ".join(sorted(names)[:8])
        suffix = "…" if len(names) > 8 else ""
        return (
            False,
            f"model {model!r} not found in Ollama (have: {available}{suffix}). "
            f"Run: ollama pull {model}",
        )

    return True, f"ollama ok; model {model!r} available"


def _tools_to_ollama(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool dicts → Ollama OpenAI-style tool definitions."""
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


def _messages_to_ollama(system_prompt: str, messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """Convert AgentForge messages to Ollama chat messages."""
    ollama_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
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
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": dict(block.input),
                            },
                        }
                    )
            assistant: dict[str, Any] = {"role": "assistant"}
            if text_parts:
                assistant["content"] = "".join(text_parts)
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            ollama_messages.append(assistant)
            continue

        text_parts = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolResultBlock):
                tool_name = tool_id_to_name.get(block.tool_use_id, block.tool_use_id)
                ollama_messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": block.content,
                    }
                )
        if text_parts:
            ollama_messages.append({"role": "user", "content": "".join(text_parts)})

    return ollama_messages


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


def _response_from_ollama(data: dict[str, Any]) -> ModelResponse:
    message = data.get("message")
    if not isinstance(message, dict):
        raise ModelClientError("Ollama response missing 'message' object")

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
            tool_id = f"tu_{uuid.uuid4().hex[:12]}"
            blocks.append(
                ToolUseBlock(
                    id=tool_id,
                    name=name,
                    input=_parse_tool_arguments(fn.get("arguments")),
                )
            )

    raw_stop = "tool_use" if any(isinstance(b, ToolUseBlock) for b in blocks) else "end_turn"
    if raw_stop not in _KNOWN_STOP_REASONS:
        raw_stop = "end_turn"
    stop_reason = cast(Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"], raw_stop)

    usage = TokenUsage(
        input_tokens=int(data.get("prompt_eval_count") or 0),
        output_tokens=int(data.get("eval_count") or 0),
    )

    msg_id = str(data.get("created_at") or uuid.uuid4())
    return ModelResponse(
        id=msg_id,
        content=blocks,
        stop_reason=stop_reason,
        usage=usage,
    )


@dataclass
class OllamaStreamDiagnostics:
    """Timing and partial-output capture from :meth:`OllamaModelClient.diagnose_stream`."""

    streamed: bool
    silent_before_first_token: bool
    hung_before_first_token: bool
    time_to_first_token_seconds: float | None
    total_elapsed_seconds: float
    generated_chars: int
    generated_text: str
    char_timeline: list[dict[str, Any]] = field(default_factory=list)
    ollama_stats: dict[str, Any] = field(default_factory=dict)
    tokens_per_second: float | None = None
    timed_out: bool = False
    error: str | None = None


__all__ = ["OllamaModelClient", "OllamaStreamDiagnostics", "check_ollama_ready"]
