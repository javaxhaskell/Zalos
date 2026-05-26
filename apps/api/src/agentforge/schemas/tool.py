"""Tool registry, invocation, observation, and approval schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from agentforge.schemas.common import (
    ApprovalStatus,
    ErrorCode,
    RiskLevel,
    StrictModel,
    ToolPhase,
)


class ToolDefinition(StrictModel):
    """Static description of a registered tool, exposed to the model.

    The registry is the security boundary. Only tools listed here can run.
    """

    name: str
    description: str
    input_schema_name: str
    """Name of the Pydantic class in ``agentforge.schemas`` validating tool args."""
    output_schema_name: str
    risk_level: RiskLevel
    requires_approval: bool
    idempotent: bool
    phases: list[ToolPhase]
    """Workflow-phase enum values where this tool is enabled."""
    authorize_callable: str
    """Dotted import path of the authorize callable."""
    adr_override: str | None = None
    """ADR ID if requires_approval=False on a write tool."""


class ToolInvocation(StrictModel):
    """One tool dispatch record."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    step: int
    tool_name: str
    args_hash: str
    """sha256(canonical_json(args)) — raw args stored separately with ACL."""
    idempotency_key: str
    """sha256(session_id + tool_name + step + canonical_json(args))."""
    attempt: int = 1
    started_at: datetime
    ended_at: datetime | None = None
    success: bool | None = None


class ToolObservation(StrictModel):
    """Result of a tool dispatch."""

    invocation_id: UUID
    success: bool
    output_summary: str
    output_path: str | None = None
    error_code: ErrorCode | None = None
    error_message: str | None = None
    latency_ms: int


class ApprovalRequest(StrictModel):
    """Pending approval that pauses the agent loop."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    step: int
    kind: str
    """Short label: 'confirm_requirements', 'review_diff', 'run_consent', etc."""
    business_summary: str
    """One-paragraph plain-English summary of what will happen on Approve."""
    diff_paths: list[str] = Field(default_factory=list)
    """Files whose diffs the user can inspect via the technical-detail panel."""
    created_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING


class ApprovalDecision(StrictModel):
    """User's response to an ApprovalRequest."""

    id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    status: ApprovalStatus
    reason: str | None = None
    decided_at: datetime
    decided_by: str
