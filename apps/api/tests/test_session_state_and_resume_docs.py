"""Honest session state / resume documentation and UI copy guards."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from agentforge.fault_tolerance.user_mitigation import build_failure_mitigation
from agentforge.schemas.common import ErrorCode, SessionStatus, Workflow
from agentforge.schemas.event import EventKind, WorkspaceEvent

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_PATH = REPO_ROOT / "docs" / "SESSION_STATE_AND_RESUME.md"
WEB_ROOT = REPO_ROOT / "apps" / "web"
FAILURE_MITIGATION_TS = WEB_ROOT / "src" / "lib" / "failure-mitigation.ts"
FAILURE_MITIGATION_ACTIONS_TSX = WEB_ROOT / "src" / "lib" / "failure-mitigation-actions.tsx"
RESUME_BANNER_TSX = WEB_ROOT / "src" / "components" / "resume-banner.tsx"


def _event(*, kind: EventKind, payload: dict | None = None) -> WorkspaceEvent:
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


def test_session_state_and_resume_doc_exists_and_covers_retry_semantics() -> None:
    assert DOCS_PATH.is_file(), f"missing {DOCS_PATH}"
    text = DOCS_PATH.read_text(encoding="utf-8").lower()

    required_phrases = [
        "retry with same inputs",
        "full rerun",
        "not continuation from an arbitrary internal",
        "can_resume",
        "always `false`",
        "events.jsonl",
        "source of truth",
        "sessionstorage",
        "expense review",
        "workflow_interrupted",
        "paused_user",
        "paused_approval",
    ]
    for phrase in required_phrases:
        assert phrase in text, f"docs missing required phrase: {phrase!r}"


def test_session_state_doc_lists_author_and_repair_persistence() -> None:
    text = DOCS_PATH.read_text(encoding="utf-8")
    for heading in (
        "## What persists — Author workflow",
        "## What persists — Repair workflow",
    ):
        assert heading in text
    for artifact in ("manifest.json", "archive.zip", "validation"):
        assert artifact in text


def test_retry_action_copy_does_not_use_resume_label() -> None:
    source = FAILURE_MITIGATION_TS.read_text(encoding="utf-8")
    retry_block = source.split("retry_same_inputs:")[1].split("},")[0]
    label_line = next(line for line in retry_block.splitlines() if "label:" in line)
    assert "Retry with same inputs" in label_line
    assert "resume" not in label_line.lower()


def test_failure_mitigation_actions_wires_retry_same_inputs_to_run() -> None:
    source = FAILURE_MITIGATION_ACTIONS_TSX.read_text(encoding="utf-8")
    assert 'action === "retry_same_inputs"' in source
    assert "runSession(sessionId" in source
    assert "RetrySameInputsButton" in source
    assert "unavailable" in source


def test_failure_mitigation_actions_wires_open_technical_details() -> None:
    source = FAILURE_MITIGATION_ACTIONS_TSX.read_text(encoding="utf-8")
    assert 'action === "open_technical_details"' in source
    assert "openTechnicalDetails" in source
    assert "OpenTechnicalDetailsButton" in source


def test_resume_banner_user_copy_avoids_resume_as_retry() -> None:
    source = RESUME_BANNER_TSX.read_text(encoding="utf-8")
    assert "Returned to saved session" in source
    assert "Retry with same inputs" in source
    # User-visible strings in describeNextStep should not say "resume".
    next_step_fn = source.split("function describeNextStep")[1]
    user_strings = re.findall(r'return "([^"]+)"', next_step_fn)
    for line in user_strings:
        assert "resume" not in line.lower(), f"user copy uses resume: {line!r}"


@pytest.mark.parametrize(
    "code,status",
    [
        (ErrorCode.GENERATED_PYTEST_FAILED, SessionStatus.FAILED_OTHER),
        (ErrorCode.SANDBOX_CRASH, SessionStatus.FAILED_SANDBOX),
        (ErrorCode.BUDGET_EXHAUSTED_TOKENS, SessionStatus.FAILED_BUDGET),
    ],
)
def test_failure_mitigation_can_resume_stays_false(
    code: ErrorCode,
    status: SessionStatus,
) -> None:
    mitigation = build_failure_mitigation(
        session_id=uuid4(),
        workflow=Workflow.AUTHOR,
        status=status,
        terminal_error_code=code,
        events=[
            _event(
                kind=EventKind.WORKFLOW_FAILED,
                payload={"error_code": code.value, "message": "test"},
            ),
        ],
        workspace=None,
    )
    assert mitigation is not None
    assert mitigation.can_resume is False


def test_budget_failure_summary_uses_progress_saved_wording() -> None:
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
    summary = mitigation.user_summary.lower()
    assert "progress saved" in summary
    assert "artifacts and events" in summary
