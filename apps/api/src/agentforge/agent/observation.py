"""Typed observations the agent loop surfaces back to the model.

OpenHands' action/observation pattern: each model turn proposes one or
more actions (tool calls); the loop dispatches and returns observations.
An observation is anything the model needs to see to decide its next
step — a successful tool result, a validation failure, a budget warning,
or an unregistered-tool rejection.

INV-8 governs *boundary* types: API request/response, persisted events,
tool input/output. The :class:`Observation` payload below is internal
control plane — it crosses module boundaries but never the network or
the database. We accept a ``dict[str, Any]`` payload here because:

  * Pydantic models go in and out via ``model_dump(mode='json')`` —
    we never lose the typed source.
  * Persisting an observation goes through :func:`EventLog.append`,
    where the payload is serialised against the canonical
    :class:`ToolObservation` schema.
  * Feeding the observation to the model means JSON-encoding it — the
    intermediate ``dict`` shape is exactly what the model sees.

The discriminator is :class:`ObservationKind`; the loop branches on
``kind`` rather than inspecting ``payload`` structure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from agentforge.schemas import ErrorCode, StrictModel


class ObservationKind(StrEnum):
    """Closed enum for the discriminator on :class:`Observation`."""

    SUCCESS = "success"
    """Tool dispatched and returned a typed output."""
    CACHE_HIT = "cache_hit"
    """Idempotency cache returned a prior observation; tool not re-run."""
    VALIDATION_FAILED = "validation_failed"
    """Model emitted args that failed Pydantic validation against the tool's input schema."""
    NOT_REGISTERED = "not_registered"
    """Model emitted a tool_use for a name that is not in the registry."""
    FORBIDDEN = "forbidden"
    """The tool's authorize callable raised :class:`ForbiddenError`."""
    HANDLER_ERROR = "handler_error"
    """The tool handler raised an exception during dispatch."""
    BUDGET_EXHAUSTED = "budget_exhausted"
    """A budget hard cap (tokens, tool calls, steps, wall) fired before dispatch."""
    APPROVAL_REQUIRED = "approval_required"
    """Tool requires approval and no APPROVAL_GRANTED event exists for this step.
    The loop pauses; the executor returns control to the caller."""


class Observation(StrictModel):
    """One observation the loop emits back to the model + the audit trail."""

    kind: ObservationKind
    tool_name: str | None = None
    """``None`` for kinds that fire before tool lookup succeeds."""
    payload: dict[str, Any] = Field(default_factory=dict)
    """JSON-safe dict; on SUCCESS this is the tool's typed output via
    ``model_dump(mode='json')``. On error kinds this is a small envelope
    with ``error_code`` + ``message`` + optional context."""
    error_code: ErrorCode | None = None
    error_message: str | None = None
    latency_ms: int = 0
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def is_terminal(self) -> bool:
        """Did this observation force the loop to stop dispatching this turn?"""
        return self.kind in {
            ObservationKind.BUDGET_EXHAUSTED,
            ObservationKind.APPROVAL_REQUIRED,
        }


def success(
    *, tool_name: str, payload: dict[str, Any], latency_ms: int
) -> Observation:
    return Observation(
        kind=ObservationKind.SUCCESS,
        tool_name=tool_name,
        payload=payload,
        latency_ms=latency_ms,
    )


def cache_hit(
    *, tool_name: str, payload: dict[str, Any]
) -> Observation:
    return Observation(
        kind=ObservationKind.CACHE_HIT,
        tool_name=tool_name,
        payload=payload,
    )


def validation_failed(
    *, tool_name: str, message: str, errors: list[dict[str, Any]]
) -> Observation:
    return Observation(
        kind=ObservationKind.VALIDATION_FAILED,
        tool_name=tool_name,
        error_message=message,
        payload={"validation_errors": errors},
    )


def not_registered(*, tool_name: str, available: list[str]) -> Observation:
    return Observation(
        kind=ObservationKind.NOT_REGISTERED,
        tool_name=tool_name,
        error_code=ErrorCode.TOOL_NOT_REGISTERED,
        error_message=f"tool {tool_name!r} is not registered in this phase",
        payload={"available_tools": sorted(available)},
    )


def forbidden(*, tool_name: str, message: str) -> Observation:
    return Observation(
        kind=ObservationKind.FORBIDDEN,
        tool_name=tool_name,
        error_message=message,
    )


def handler_error(
    *,
    tool_name: str,
    error_code: ErrorCode,
    message: str,
    latency_ms: int,
) -> Observation:
    return Observation(
        kind=ObservationKind.HANDLER_ERROR,
        tool_name=tool_name,
        error_code=error_code,
        error_message=message,
        latency_ms=latency_ms,
    )


def budget_exhausted(*, error_code: ErrorCode, message: str) -> Observation:
    return Observation(
        kind=ObservationKind.BUDGET_EXHAUSTED,
        error_code=error_code,
        error_message=message,
    )


def approval_required(*, tool_name: str, request_id: str) -> Observation:
    return Observation(
        kind=ObservationKind.APPROVAL_REQUIRED,
        tool_name=tool_name,
        payload={"approval_request_id": request_id},
    )
