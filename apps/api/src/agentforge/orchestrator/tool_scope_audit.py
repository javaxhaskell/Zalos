"""Honest audit events for backend-orchestrated capabilities.

Orchestrator-owned steps (file profiling, sandbox execution, validation,
archives) do not pass through :class:`agentforge.agent.loop.AgentLoop` and
must not be recorded as ``TOOL_INVOKED`` — that would imply the model
dispatched a registered tool. Instead we append ``DECISION_INPUT`` events
whose payload ``kind`` is ``tool_action_recorded`` with explicit
``controlled_by`` and ``dispatch_mode`` metadata.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from agentforge.persistence.event_log import EventLog
from agentforge.schemas import ActorType, EventKind, Workflow, WorkspaceEvent

TOOL_ACTION_RECORDED = "tool_action_recorded"
DEFAULT_APPROVAL_POLICY = "auto_approved_backend_validation"

ControlledBy = Literal["backend", "model", "generated_code"]
DispatchMode = Literal["orchestrator", "agent_loop"]
ToolActionStatus = Literal["started", "completed", "failed"]

# Model authoring ``purpose`` values → audit tool names.
_MODEL_PURPOSE_TOOL_NAMES: dict[str, str] = {
    "contract_planning": "contract_planning",
    "contract_review": "contract_review",
    "contract_schema_repair": "schema_repair",
    "code_generation": "code_generation",
    "code_adaptation": "code_generation",
    "test_generation": "test_generation",
    "validation_check_generation": "test_generation",
    "safety_repair": "safety_scan",
    "execution_repair": "execution_repair",
    "json_repair": "json_repair",
    "missing_file_repair": "missing_file_repair",
    "missing_files_repair": "missing_file_repair",
}


def model_purpose_tool_name(purpose: str) -> str:
    """Map an authoring model ``purpose`` to a stable audit tool name."""
    return _MODEL_PURPOSE_TOOL_NAMES.get(purpose, purpose)


def record_tool_action(
    *,
    event_log: EventLog,
    session_id: UUID,
    step: int,
    tool_name: str,
    workflow: Workflow | str,
    phase: str,
    controlled_by: ControlledBy,
    dispatch_mode: DispatchMode,
    status: ToolActionStatus,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    approval_policy: str = DEFAULT_APPROVAL_POLICY,
    detail: str | None = None,
) -> None:
    """Append a ``tool_action_recorded`` decision-input audit line."""
    workflow_value = workflow.value if isinstance(workflow, Workflow) else workflow
    payload: dict[str, Any] = {
        "kind": TOOL_ACTION_RECORDED,
        "tool_name": tool_name,
        "workflow": workflow_value,
        "phase": phase,
        "controlled_by": controlled_by,
        "dispatch_mode": dispatch_mode,
        "status": status,
        "inputs": list(inputs or []),
        "outputs": list(outputs or []),
        "approval_policy": approval_policy,
    }
    if detail:
        payload["detail"] = detail
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload=payload,
        step=step,
    )


def tool_action_events(events: list[WorkspaceEvent]) -> list[dict[str, Any]]:
    """Return payloads for orchestrated tool-action audit events."""
    collected: list[dict[str, Any]] = []
    for event in events:
        if event.kind != EventKind.DECISION_INPUT:
            continue
        payload = event.payload or {}
        if payload.get("kind") == TOOL_ACTION_RECORDED:
            collected.append(payload)
    return collected


def record_model_orchestrated_call(
    *,
    event_log: EventLog,
    session_id: UUID,
    step: int,
    purpose: str,
    phase: str,
    status: ToolActionStatus,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    detail: str | None = None,
) -> None:
    """Audit a bounded model stage invoked by the Author orchestrator."""
    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name=model_purpose_tool_name(purpose),
        workflow=Workflow.AUTHOR,
        phase=phase,
        controlled_by="model",
        dispatch_mode="orchestrator",
        status=status,
        inputs=inputs,
        outputs=outputs,
        detail=detail,
    )


__all__ = [
    "DEFAULT_APPROVAL_POLICY",
    "TOOL_ACTION_RECORDED",
    "model_purpose_tool_name",
    "record_model_orchestrated_call",
    "record_tool_action",
    "tool_action_events",
]
