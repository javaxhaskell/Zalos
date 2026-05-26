"""Session-level schemas: row, list, manifest, budget."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from agentforge.schemas.common import (
    ErrorCode,
    SessionStatus,
    StrictModel,
    Workflow,
)


class ArtifactSummary(StrictModel):
    """Per-artifact entry rolled into the manifest at finalisation."""

    artifact_type: str
    path: str
    size_bytes: int | None = None
    hash_sha256: str | None = None


class ValidationLayerSummary(StrictModel):
    """One layer's outcome rolled into the manifest."""

    layer: str
    status: str  # "pass" | "fail" | "skipped"


class CompletionFailureMetadata(StrictModel):
    """Diagnostic metadata for stopped custom workflow builds."""

    error_code: str | None = None
    failed_layer: str | None = None
    failed_check: str | None = None
    validation_failures: list[str] = Field(default_factory=list)
    pytest_summary: str | None = None
    pytest_passed: int | None = None
    pytest_failed: int | None = None


class CompletionMetadata(StrictModel):
    """Provenance + reproducibility metadata persisted on the manifest.

    Written once at terminal status; downstream consumers (the
    SESSION_README, the audit endpoints, the wizard's provenance card)
    read from here rather than re-aggregating the event stream.
    """

    template_name: str | None = None
    workflow_type: str | None = None
    build_mode: str | None = None
    completion_via: str | None = None
    """Value of ``workflow_completed.payload.via``."""
    recovery_used: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    model_call_count: int = 0
    model_contributed: bool = False
    model_stages: list[str] = Field(default_factory=list)
    model_contributed_files: list[str] = Field(default_factory=list)
    authoring_provenance_artifacts: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    artifacts: list[ArtifactSummary] = Field(default_factory=list)
    validation_summary: list[ValidationLayerSummary] = Field(default_factory=list)
    validation_overall: str | None = None  # "pass" | "fail" | None
    validation_passed: bool = False
    tests_passed: bool = False
    output_hash: str | None = None
    output_path: str | None = None
    input_file: str | None = None
    input_format: str | None = None
    selected_sheet: str | None = None
    normalized_input_path: str | None = None
    tests_path: str | None = None
    skipped_layers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_report_hash: str | None = None
    validation_report_path: str | None = None
    """System validation report path (AgentForge audit layers)."""
    workflow_report_path: str | None = None
    """User-facing workflow report path when the contract requires one."""
    workflow_report_hash: str | None = None
    # Repair-specific (populated when ``completion_via=repair_validated_patch``)
    repair_reproduced: bool | None = None
    patch_applied: bool | None = None
    post_fix_tests_passed: bool | None = None
    changed_files: list[str] = Field(default_factory=list)
    before_fix_pytest_summary: str | None = None
    after_fix_pytest_summary: str | None = None
    repair_report_path: str | None = None
    repair_report_json_path: str | None = None
    patch_diff_path: str | None = None
    before_fix_log_path: str | None = None
    after_fix_log_path: str | None = None
    failure: CompletionFailureMetadata | None = None
    """Populated when a custom workflow build stops before completion."""


class MitigationAction(StrEnum):
    """User-facing mitigation actions for failed sessions."""

    START_NEW_SESSION = "start_new_session"
    EDIT_WORKFLOW_DESCRIPTION = "edit_workflow_description"
    UPLOAD_REPLACEMENT_FILE = "upload_replacement_file"
    ANSWER_CLARIFICATION = "answer_clarification"
    RETRY_SAME_INPUTS = "retry_same_inputs"
    DOWNLOAD_AUDIT_PACKAGE = "download_audit_package"
    OPEN_TECHNICAL_DETAILS = "open_technical_details"
    OPEN_REPAIR = "open_repair"
    PROVIDE_EXPECTED_OUTPUT = "provide_expected_output"
    SIMPLIFY_WORKFLOW = "simplify_workflow"
    INCREASE_BUDGET = "increase_budget"
    UPLOAD_PROBLEM_REPORT = "upload_problem_report"


class FailureMitigation(StrictModel):
    """Computed guidance for finance users when a session ends in failure."""

    user_title: str
    user_summary: str
    what_we_found: str | None = None
    evidence_items: list[str] = Field(default_factory=list)
    mitigation_actions: list[MitigationAction] = Field(default_factory=list)
    primary_action: MitigationAction
    secondary_actions: list[MitigationAction] = Field(default_factory=list)
    retry_safe: bool = False
    can_resume: bool = False
    can_download_archive: bool = False
    technical_details_ref: str = "events"
    failure_category: str


class BudgetStatus(StrictModel):
    """Per-session budget counters and limits."""

    tokens_used: int = 0
    tokens_limit: int = 150_000
    tool_calls_used: int = 0
    tool_calls_limit: int = 40
    steps_used: int = 0
    steps_limit: int = 25
    wall_seconds_used: int = 0
    wall_seconds_limit: int = 1_500
    file_count: int = 0
    file_count_limit: int = 20


class SessionCreate(StrictModel):
    """Request body: ``POST /sessions``."""

    workflow: Workflow


class Session(StrictModel):
    """Full session row."""

    id: UUID = Field(default_factory=uuid4)
    workflow: Workflow
    status: SessionStatus = SessionStatus.CREATED
    current_phase: str | None = None
    current_step: int = 0
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    workspace_path: str
    manifest_schema_version: int = 1
    budget: BudgetStatus = Field(default_factory=BudgetStatus)
    last_event_id: UUID | None = None
    terminal_error_code: ErrorCode | None = None
    failure_mitigation: FailureMitigation | None = None
    """Populated on ``GET /sessions/{id}`` for terminal failed sessions only."""


class SessionListView(StrEnum):
    """Filter for ``GET /sessions`` dashboard tabs."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class SessionListItem(StrictModel):
    """Compact session row for the dashboard list."""

    id: UUID
    workflow: Workflow
    status: SessionStatus
    current_phase: str | None
    started_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class SessionList(StrictModel):
    sessions: list[SessionListItem]


class SessionBudgetBreakdownItem(StrictModel):
    """Per-session token usage for the lifetime budget summary."""

    id: UUID
    workflow: Workflow
    tokens_used: int
    status: SessionStatus


class SessionBudgetSummary(StrictModel):
    """Aggregate token usage across all non-deleted sessions."""

    lifetime_tokens_used: int
    session_count: int
    recent_sessions: list[SessionBudgetBreakdownItem] = Field(default_factory=list)


class ResumeManifest(StrictModel):
    """Shape of ``manifest.json`` on disk for a session."""

    session_id: UUID
    workflow: Workflow
    status: SessionStatus
    current_phase: str | None
    current_step: int
    started_at: datetime
    updated_at: datetime
    workspace_path: str
    schema_version: int = 1
    budget: BudgetStatus
    file_hashes: dict[str, str] = Field(default_factory=dict)
    """Map of relative path → sha256 hash for tamper-detection on resume."""
    completion: CompletionMetadata | None = None
    """Provenance + reproducibility metadata; populated at finalisation."""


__all__ = (
    "ArtifactSummary",
    "BudgetStatus",
    "CompletionFailureMetadata",
    "CompletionMetadata",
    "FailureMitigation",
    "MitigationAction",
    "ResumeManifest",
    "Session",
    "SessionBudgetBreakdownItem",
    "SessionBudgetSummary",
    "SessionCreate",
    "SessionList",
    "SessionListItem",
    "SessionListView",
    "ValidationLayerSummary",
)


def _drop_pydantic_extras_warning(_: Any) -> None:
    """Placeholder to keep the import-time linter happy."""
