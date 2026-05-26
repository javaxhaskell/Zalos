"""Workspace event log schema.

Events are written to ``${workspace_path}/events.jsonl`` one per line.
The kind is a discriminator over typed payloads; adding a new kind
requires an ADR (see CONTRACTS.md §9).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from agentforge.schemas.common import ActorType, StrictModel


class EventKind(StrEnum):
    """Closed enum of event kinds. Adding requires ADR."""

    WORKFLOW_STARTED = "workflow_started"
    WORKSPACE_ALLOCATED = "workspace_allocated"
    TEMPLATE_SEEDED = "template_seeded"
    FILE_UPLOADED = "file_uploaded"
    SCHEMA_DETECTED = "schema_detected"
    DECISION_INPUT = "decision_input"
    QUESTION_ASKED = "question_asked"
    ANSWER_RECEIVED = "answer_received"
    REQUIREMENTS_DRAFTED = "requirements_drafted"
    REQUIREMENTS_CONFIRMED = "requirements_confirmed"
    PHASE_TRANSITIONED = "phase_transitioned"
    MODEL_CALLED = "model_called"
    TOOL_INVOKED = "tool_invoked"
    TOOL_OBSERVED = "tool_observed"
    FILE_WRITTEN = "file_written"
    PATCH_APPLIED = "patch_applied"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DECLINED = "approval_declined"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    TEST_RUN_STARTED = "test_run_started"
    TEST_RUN_COMPLETED = "test_run_completed"
    VALIDATION_RUN = "validation_run"
    ARTIFACT_GENERATED = "artifact_generated"
    BUDGET_WARNED = "budget_warned"
    BUDGET_EXHAUSTED = "budget_exhausted"
    REPAIR_PROBLEM_RECEIVED = "repair_problem_received"
    AGENT_SUMMARY_PRODUCED = "agent_summary_produced"
    REPRODUCTION_RESULT = "reproduction_result"
    DIAGNOSIS_PRODUCED = "diagnosis_produced"
    PATCH_PROPOSED = "patch_proposed"
    REPAIR_REPORT_GENERATED = "repair_report_generated"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    SESSION_AUTO_ARCHIVED = "session_auto_archived"


class WorkspaceEvent(StrictModel):
    """One line of ``events.jsonl``.

    The ``payload`` is intentionally typed loosely here so the event log
    can serialise discriminated payloads without coupling this module to
    every payload class. Validation of the typed payload happens at the
    emission site (``event_log.append``) and on read.
    """

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    ts: datetime
    step: int
    kind: EventKind
    actor_type: ActorType
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_event_id: UUID | None = None
