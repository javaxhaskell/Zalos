"""Tests for fault-tolerance user mitigation mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from agentforge.fault_tolerance.user_mitigation import build_failure_mitigation
from agentforge.schemas.common import ErrorCode, SessionStatus, Workflow
from agentforge.schemas.event import EventKind, WorkspaceEvent
from agentforge.schemas.session import MitigationAction


def _event(
    *,
    kind: EventKind,
    payload: dict | None = None,
) -> WorkspaceEvent:
    now = datetime.now(UTC)
    return WorkspaceEvent(
        id=uuid4(),
        session_id=uuid4(),
        ts=now,
        step=0,
        kind=kind,
        actor_type="system",
        payload=payload or {},
    )


def _failed_session_kwargs(
    code: ErrorCode,
    *,
    workflow: Workflow = Workflow.AUTHOR,
    payload: dict | None = None,
) -> dict:
    sid = uuid4()
    wf_payload = {
        "error_code": code.value,
        "message": "test failure",
        **(payload or {}),
    }
    events = [
        _event(kind=EventKind.WORKFLOW_FAILED, payload=wf_payload),
    ]
    return {
        "session_id": sid,
        "workflow": workflow,
        "status": SessionStatus.FAILED_OTHER,
        "terminal_error_code": code,
        "events": events,
        "workspace": None,
    }


def _assert_finance_friendly(mitigation) -> None:
    blob = " ".join(
        part
        for part in (
            mitigation.user_title,
            mitigation.user_summary,
            mitigation.what_we_found or "",
        )
        if part
    ).lower()
    for term in (
        "pytest",
        "model hash",
        "artifact",
        "author_output_contract",
        "provenance",
    ):
        assert term not in blob, f"forbidden term {term!r} in user-facing copy"


def _assert_core_fields(mitigation) -> None:
    assert mitigation.user_title.strip()
    assert mitigation.user_summary.strip()
    assert mitigation.primary_action in mitigation.mitigation_actions
    assert mitigation.technical_details_ref in {"events", "manifest", "validation_report"}


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.GENERATED_PYTEST_FAILED,
        ErrorCode.MALFORMED_CSV,
        ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED,
        ErrorCode.SAFETY_VALIDATION_FAILED,
        ErrorCode.BUDGET_EXHAUSTED_TOKENS,
        ErrorCode.REPAIR_CANNOT_REPRODUCE,
        ErrorCode.SANDBOX_CRASH,
        ErrorCode.UNIVERSAL_VALIDATION_FAILED,
        ErrorCode.GOLDEN_OUTPUT_COMPARISON_FAILED,
    ],
)
def test_mapped_failures_have_title_summary_mitigation_and_technical_ref(code: ErrorCode) -> None:
    mitigation = build_failure_mitigation(**_failed_session_kwargs(code))
    assert mitigation is not None
    _assert_core_fields(mitigation)
    _assert_finance_friendly(mitigation)
    assert len(mitigation.mitigation_actions) >= 1


def test_generated_pytest_failed_includes_test_evidence(tmp_path: Path) -> None:
    sid = uuid4()
    (tmp_path / "generated" / "tests").mkdir(parents=True)
    (tmp_path / "generated" / "tests" / "test_agent.py").write_text("def test_x(): pass\n")
    (tmp_path / "generated" / "agent.py").write_text("def main(): pass\n")
    (tmp_path / "archive.zip").write_bytes(b"PK")

    events = [
        _event(
            kind=EventKind.WORKFLOW_FAILED,
            payload={
                "error_code": ErrorCode.GENERATED_PYTEST_FAILED.value,
                "message": "pytest gate failed",
                "pytest_summary": "3 failed, 0 passed",
                "failed_check": "test_exception_rows",
            },
        ),
    ]
    mitigation = build_failure_mitigation(
        session_id=sid,
        workflow=Workflow.AUTHOR,
        status=SessionStatus.FAILED_OTHER,
        terminal_error_code=ErrorCode.GENERATED_PYTEST_FAILED,
        events=events,
        workspace=tmp_path,
    )
    assert mitigation is not None
    assert mitigation.failure_category == "pytest"
    assert "quality checks" in mitigation.user_summary.lower()
    assert mitigation.what_we_found == "One or more automated quality checks did not pass."
    assert "Audit package" in mitigation.evidence_items
    assert MitigationAction.PROVIDE_EXPECTED_OUTPUT in mitigation.mitigation_actions
    assert mitigation.can_download_archive is True
    _assert_finance_friendly(mitigation)


def test_malformed_file_maps_to_upload_replacement() -> None:
    mitigation = build_failure_mitigation(
        **_failed_session_kwargs(ErrorCode.MALFORMED_CSV),
    )
    assert mitigation is not None
    assert mitigation.failure_category == "upload"
    assert mitigation.primary_action == MitigationAction.UPLOAD_REPLACEMENT_FILE


def test_contract_failure_suggests_clarify_and_expected_output() -> None:
    mitigation = build_failure_mitigation(
        **_failed_session_kwargs(ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED),
    )
    assert mitigation is not None
    assert mitigation.failure_category == "model_contract"
    assert MitigationAction.EDIT_WORKFLOW_DESCRIPTION in mitigation.mitigation_actions
    assert MitigationAction.PROVIDE_EXPECTED_OUTPUT in mitigation.mitigation_actions


def test_safety_failure_does_not_suggest_bypass() -> None:
    mitigation = build_failure_mitigation(
        **_failed_session_kwargs(
            ErrorCode.SAFETY_VALIDATION_FAILED,
            payload={"failed_check": "unsafe eval() usage blocked"},
        ),
    )
    assert mitigation is not None
    assert mitigation.primary_action == MitigationAction.EDIT_WORKFLOW_DESCRIPTION
    assert MitigationAction.OPEN_TECHNICAL_DETAILS in mitigation.mitigation_actions


def test_budget_limit_maps_to_simplify_and_increase_budget() -> None:
    mitigation = build_failure_mitigation(
        session_id=uuid4(),
        workflow=Workflow.AUTHOR,
        status=SessionStatus.FAILED_BUDGET,
        terminal_error_code=ErrorCode.BUDGET_EXHAUSTED_TOKENS,
        events=[
            _event(
                kind=EventKind.BUDGET_EXHAUSTED,
                payload={"kind": "tokens", "error_code": ErrorCode.BUDGET_EXHAUSTED_TOKENS.value},
            ),
        ],
        workspace=None,
    )
    assert mitigation is not None
    assert mitigation.failure_category == "budget"
    assert MitigationAction.SIMPLIFY_WORKFLOW in mitigation.mitigation_actions
    assert MitigationAction.INCREASE_BUDGET in mitigation.mitigation_actions


def test_repair_cannot_reproduce_maps_to_problem_report_upload() -> None:
    mitigation = build_failure_mitigation(
        **_failed_session_kwargs(
            ErrorCode.REPAIR_CANNOT_REPRODUCE,
            workflow=Workflow.REPAIR,
            payload={"discovered_issue_summary": "31-day boundary mismatch"},
        ),
    )
    assert mitigation is not None
    assert mitigation.primary_action == MitigationAction.UPLOAD_PROBLEM_REPORT
    assert mitigation.what_we_found == "31-day boundary mismatch"


def test_sandbox_crash_maps_to_runtime_and_audit_download(tmp_path: Path) -> None:
    (tmp_path / "archive.zip").write_bytes(b"PK")
    mitigation = build_failure_mitigation(
        session_id=uuid4(),
        workflow=Workflow.AUTHOR,
        status=SessionStatus.FAILED_SANDBOX,
        terminal_error_code=ErrorCode.SANDBOX_CRASH,
        events=[
            _event(
                kind=EventKind.WORKFLOW_FAILED,
                payload={
                    "error_code": ErrorCode.SANDBOX_CRASH.value,
                    "message": "subprocess exited non-zero",
                },
            ),
        ],
        workspace=tmp_path,
    )
    assert mitigation is not None
    assert mitigation.failure_category == "sandbox"
    assert mitigation.can_download_archive is True
    assert MitigationAction.DOWNLOAD_AUDIT_PACKAGE in mitigation.mitigation_actions


def test_universal_validation_failure_maps_to_validation_tier() -> None:
    mitigation = build_failure_mitigation(
        **_failed_session_kwargs(
            ErrorCode.UNIVERSAL_VALIDATION_FAILED,
            payload={
                "failed_layer": "row_level",
                "failed_check": "Row count drift",
            },
        ),
    )
    assert mitigation is not None
    assert mitigation.failure_category == "validation"
    assert "validation checks" in mitigation.user_summary.lower()
    assert mitigation.technical_details_ref == "validation_report"
    _assert_finance_friendly(mitigation)


def test_golden_output_mismatch_maps_to_expected_output() -> None:
    mitigation = build_failure_mitigation(
        **_failed_session_kwargs(
            ErrorCode.GOLDEN_OUTPUT_COMPARISON_FAILED,
            payload={"failed_check": "golden row mismatch on amount"},
        ),
    )
    assert mitigation is not None
    assert mitigation.failure_category == "golden"
    assert mitigation.primary_action == MitigationAction.PROVIDE_EXPECTED_OUTPUT
    assert mitigation.user_title == "Output did not match expected review standards"
    assert mitigation.what_we_found == "Some rows did not match the independent quality check."
    _assert_finance_friendly(mitigation)


def test_provenance_hash_gate_maps_to_retry_and_validation_report() -> None:
    mitigation = build_failure_mitigation(
        **_failed_session_kwargs(
            ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED,
            payload={
                "message": (
                    "Final artifact `generated/author_output_contract.json` "
                    "no longer matches the recorded model hash."
                ),
                "path": "generated/author_output_contract.json",
                "stage": "pre_author_validation_gate",
            },
        ),
    )
    assert mitigation is not None
    assert mitigation.failure_category == "provenance"
    assert mitigation.primary_action == MitigationAction.RETRY_SAME_INPUTS
    assert mitigation.user_title == "Workflow could not be finalized"
    assert "validation checks" in mitigation.user_summary.lower()
    assert mitigation.technical_details_ref == "validation_report"
    assert mitigation.what_we_found == "The workflow plan file was changed after it was approved."
    assert MitigationAction.OPEN_TECHNICAL_DETAILS in mitigation.mitigation_actions
    _assert_finance_friendly(mitigation)


def test_provenance_failure_allows_retry_same_inputs() -> None:
    mitigation = build_failure_mitigation(
        **_failed_session_kwargs(ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED),
    )
    assert mitigation is not None
    assert mitigation.retry_safe is True
    assert mitigation.can_resume is False
    assert mitigation.primary_action == MitigationAction.RETRY_SAME_INPUTS


def test_provenance_hash_gate_with_archive_offers_download(tmp_path: Path) -> None:
    (tmp_path / "archive.zip").write_bytes(b"PK")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "system_validation_report.md").write_text("# PASS\n")

    mitigation = build_failure_mitigation(
        session_id=uuid4(),
        workflow=Workflow.AUTHOR,
        status=SessionStatus.FAILED_OTHER,
        terminal_error_code=ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED,
        events=[
            _event(
                kind=EventKind.WORKFLOW_FAILED,
                payload={
                    "error_code": ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED.value,
                    "message": "contract hash mismatch",
                },
            ),
        ],
        workspace=tmp_path,
    )
    assert mitigation is not None
    assert mitigation.can_download_archive is True
    assert MitigationAction.DOWNLOAD_AUDIT_PACKAGE in mitigation.mitigation_actions
    assert "Validation summary" in mitigation.evidence_items
    _assert_finance_friendly(mitigation)
