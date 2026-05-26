"""Sanity tests on the canonical Pydantic schemas.

Confirms strict mode, extra=forbid, and the basic shape of the load-bearing
contracts. Phase 1's contracts gate every downstream prompt — these tests
catch the easy mistakes early.

Note on strict mode: Pydantic v2 strict rejects string→enum coercion via
``model_validate(dict)``. The JSON-input path (``model_validate_json``) DOES
coerce strings to enums, which is the canonical API ingestion path. Tests
that simulate API input use ``model_validate_json``; tests that exercise
in-Python construction use enum/UUID instances directly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentforge.schemas import (
    ApprovalRequest,
    AuthorPhase,
    BudgetStatus,
    EventKind,
    RepairPhase,
    RiskLevel,
    Session,
    SessionCreate,
    SessionStatus,
    ToolDefinition,
    ToolPhase,
    Workflow,
)


def test_strict_mode_rejects_extra_fields_on_json_input() -> None:
    """Unknown fields are rejected on the JSON ingestion path."""
    payload = json.dumps({"workflow": "author", "unexpected_field": "x"})
    with pytest.raises(ValidationError):
        SessionCreate.model_validate_json(payload)


def test_session_create_accepts_valid_workflow_from_json() -> None:
    payload = json.dumps({"workflow": "author"})
    parsed = SessionCreate.model_validate_json(payload)
    assert parsed.workflow == Workflow.AUTHOR


def test_session_create_in_python_uses_enum_instance() -> None:
    """In-Python construction requires enum instances (not strings) in strict mode."""
    payload = SessionCreate(workflow=Workflow.AUTHOR)
    assert payload.workflow == Workflow.AUTHOR


def test_session_rejects_unknown_status_value_from_json() -> None:
    """JSON ingestion accepts known enum strings; rejects unknown ones."""
    now = datetime.now(UTC).isoformat()
    # Valid: known status
    valid_payload = json.dumps(
        {
            "id": str(uuid4()),
            "workflow": "author",
            "status": "created",
            "started_at": now,
            "updated_at": now,
            "workspace_path": "/tmp/example",
        }
    )
    Session.model_validate_json(valid_payload)
    # Invalid: unknown status
    invalid_payload = json.dumps(
        {
            "id": str(uuid4()),
            "workflow": "author",
            "status": "not_a_real_status",
            "started_at": now,
            "updated_at": now,
            "workspace_path": "/tmp/example",
        }
    )
    with pytest.raises(ValidationError):
        Session.model_validate_json(invalid_payload)


def test_budget_status_defaults_match_decision() -> None:
    bs = BudgetStatus()
    assert bs.tokens_limit == 150_000
    assert bs.tool_calls_limit == 40
    assert bs.steps_limit == 25
    assert bs.wall_seconds_limit == 1_500
    assert bs.file_count_limit == 20


def test_author_phase_enum_contains_all_14_phases() -> None:
    expected = {
        "author_template",
        "author_upload",
        "author_profile",
        "author_describe",
        "author_infer",
        "author_qa",
        "author_confirm",
        "author_generate",
        "author_review_diff",
        "author_applied",
        "author_run",
        "author_validate",
        "author_review",
        "author_finalise",
    }
    assert {p.value for p in AuthorPhase} == expected


def test_repair_phase_enum_contains_all_14_phases() -> None:
    expected = {
        "repair_upload",
        "repair_loaded",
        "repair_problem",
        "repair_triage",
        "repair_confirm_summary",
        "repair_reproduce",
        "repair_need_info",
        "repair_diagnose",
        "repair_propose",
        "repair_review_patch",
        "repair_apply",
        "repair_validate",
        "repair_report",
        "repair_finalise",
    }
    assert {p.value for p in RepairPhase} == expected


def test_session_status_enum_includes_all_terminal_states() -> None:
    terminal = {
        "completed",
        "failed_budget",
        "failed_model",
        "failed_sandbox",
        "failed_user_reject",
        "failed_other",
        "auto_archived",
    }
    all_values = {s.value for s in SessionStatus}
    assert terminal.issubset(all_values)


def test_event_kind_enum_contains_expected_events() -> None:
    """Spot-check a representative subset of the event-kind enum."""
    values = {e.value for e in EventKind}
    assert "workflow_started" in values
    assert "tool_invoked" in values
    assert "approval_granted" in values
    assert "validation_run" in values
    assert "workflow_completed" in values


def test_tool_definition_accepts_canonical_shape() -> None:
    td = ToolDefinition(
        name="inspect_csv_schema",
        description="Inspect a CSV and return its column schema.",
        input_schema_name="InspectCsvInput",
        output_schema_name="FileProfile",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=[ToolPhase.AUTHOR_INFO, ToolPhase.REPAIR_INFO],
        authorize_callable="agentforge.tools.authz.allow_authenticated",
    )
    assert td.requires_approval is False
    assert ToolPhase.AUTHOR_INFO in td.phases


def test_approval_request_business_summary_required_from_json() -> None:
    """A missing required field is rejected on the JSON ingestion path."""
    payload = json.dumps(
        {
            "id": str(uuid4()),
            "session_id": str(uuid4()),
            "step": 1,
            "kind": "review_diff",
            "created_at": datetime.now(UTC).isoformat(),
            # missing business_summary
        }
    )
    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate_json(payload)
