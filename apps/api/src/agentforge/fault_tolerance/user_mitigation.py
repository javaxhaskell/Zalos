"""Map terminal session failures to finance-user mitigation guidance.

Computed at read time for ``GET /sessions/{id}`` — not persisted separately
so the append-only event log remains the source of truth (INV-6).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import UUID

from agentforge.schemas.common import ErrorCode, SessionStatus, Workflow
from agentforge.schemas.event import EventKind, WorkspaceEvent
from agentforge.schemas.session import FailureMitigation, MitigationAction


_TERMINAL_STATUSES = frozenset(
    {
        SessionStatus.FAILED_BUDGET,
        SessionStatus.FAILED_MODEL,
        SessionStatus.FAILED_SANDBOX,
        SessionStatus.FAILED_USER_REJECT,
        SessionStatus.FAILED_OTHER,
    }
)

_UPLOAD_CODES = frozenset(
    {
        ErrorCode.MALFORMED_CSV,
        ErrorCode.UNSUPPORTED_FILE_TYPE,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.UPLOAD_LIMIT_EXCEEDED,
    }
)

_SCHEMA_CODES = frozenset(
    {
        ErrorCode.MISSING_REQUIRED_COLUMNS,
        ErrorCode.AMBIGUOUS_SCHEMA,
        ErrorCode.AUTHOR_INTENT_SCHEMA_MISMATCH,
        ErrorCode.AUTHOR_CUSTOM_WORKFLOW_NOT_VALIDATED,
    }
)

_CONTRACT_CODES = frozenset(
    {
        ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED,
        ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED,
        ErrorCode.AUTHOR_CONTRACT_CLARIFICATION_REQUIRED,
    }
)

_CODEGEN_CODES = frozenset(
    {
        ErrorCode.AUTHOR_CODE_GENERATION_FAILED,
        ErrorCode.AUTHOR_TEST_GENERATION_FAILED,
        ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
        ErrorCode.GENERATED_CODE_FAILED,
        ErrorCode.SAFETY_VALIDATION_FAILED,
        ErrorCode.ARTIFACT_VALIDATION_FAILED,
    }
)

_VALIDATION_CODES = frozenset(
    {
        ErrorCode.AUTHOR_VALIDATION_FAILED,
        ErrorCode.AUTHOR_CUSTOM_BUILD_FAILED,
        ErrorCode.UNIVERSAL_VALIDATION_FAILED,
        ErrorCode.CONTRACT_SPECIFIC_VALIDATION_FAILED,
        ErrorCode.GENERATED_PYTEST_FAILED,
        ErrorCode.GOLDEN_OUTPUT_COMPARISON_FAILED,
    }
)

_BUDGET_CODES = frozenset(
    {
        ErrorCode.BUDGET_EXHAUSTED_TOKENS,
        ErrorCode.BUDGET_EXHAUSTED_TOOL_CALLS,
        ErrorCode.BUDGET_EXHAUSTED_STEPS,
        ErrorCode.BUDGET_EXHAUSTED_WALL_TIME,
        ErrorCode.BUDGET_EXHAUSTED_FILE_COUNT,
    }
)

_PROVENANCE_CODES = frozenset(
    {
        ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED,
        ErrorCode.AUTHOR_NON_LLM_BUSINESS_LOGIC_DETECTED,
    }
)

_EVIDENCE_FILE_LABELS: tuple[tuple[str, str], ...] = (
    ("reports/system_validation_report.md", "Validation summary"),
    ("reports/validation_report.md", "Validation summary"),
    ("outputs/output.csv", "Processed results file"),
    ("outputs/exceptions.csv", "Flagged exceptions file"),
    ("archive.zip", "Audit package"),
)

_FINANCE_FORBIDDEN_TERMS = (
    "pytest",
    "model hash",
    "artifact",
    "author_output_contract",
    "provenance",
)


def build_failure_mitigation(
    *,
    session_id: UUID,
    workflow: Workflow,
    status: SessionStatus,
    terminal_error_code: ErrorCode | None,
    events: list[WorkspaceEvent],
    workspace: Path | None = None,
) -> FailureMitigation | None:
    """Return mitigation guidance for terminal failed sessions."""
    if status not in _TERMINAL_STATUSES:
        return None

    wf_payload = _latest_workflow_failed_payload(events)
    code = terminal_error_code
    if code is None and wf_payload:
        raw = wf_payload.get("error_code")
        if isinstance(raw, str):
            try:
                code = ErrorCode(raw)
            except ValueError:
                code = ErrorCode.UNKNOWN

    if status == SessionStatus.FAILED_BUDGET and code is None:
        code = _budget_code_from_events(events) or ErrorCode.BUDGET_EXHAUSTED_TOKENS
    if status == SessionStatus.FAILED_MODEL and code is None:
        code = ErrorCode.UNKNOWN
    if status == SessionStatus.FAILED_SANDBOX and code is None:
        code = ErrorCode.SANDBOX_CRASH

    evidence = _collect_evidence_items(events, workspace)
    raw_what_we_found = _extract_what_we_found(wf_payload, events)
    what_we_found = _humanize_what_we_found(raw_what_we_found, code)
    has_archive = _workspace_has_archive(workspace)
    has_repair_package = _workspace_has_repair_package(workspace)

    spec = _resolve_spec(
        workflow=workflow,
        status=status,
        code=code,
        wf_payload=wf_payload,
        what_we_found=what_we_found,
        has_archive=has_archive,
        has_repair_package=has_repair_package,
    )

    actions = list(spec["actions"])
    primary = spec["primary"]
    secondary = [a for a in actions if a != primary]

    return FailureMitigation(
        user_title=spec["title"],
        user_summary=spec["summary"],
        what_we_found=what_we_found,
        evidence_items=evidence,
        mitigation_actions=actions,
        primary_action=primary,
        secondary_actions=secondary,
        retry_safe=spec["retry_safe"],
        can_resume=False,
        can_download_archive=has_archive,
        technical_details_ref=spec["technical_ref"],
        failure_category=spec["category"],
    )


def _resolve_spec(
    *,
    workflow: Workflow,
    status: SessionStatus,
    code: ErrorCode | None,
    wf_payload: dict[str, Any] | None,
    what_we_found: str | None,
    has_archive: bool,
    has_repair_package: bool,
) -> dict[str, Any]:
    if code in _UPLOAD_CODES:
        return _upload_spec(code)
    if code in _SCHEMA_CODES:
        return _schema_spec(code)
    if code == ErrorCode.AUTHOR_CONTRACT_CLARIFICATION_REQUIRED:
        return _clarification_spec()
    if code in _CONTRACT_CODES:
        return _contract_spec(code, has_archive)
    if code == ErrorCode.SAFETY_VALIDATION_FAILED:
        return _safety_spec(what_we_found, has_archive)
    if code in _CODEGEN_CODES:
        return _codegen_spec(code, workflow, has_archive, has_repair_package)
    if code == ErrorCode.GENERATED_PYTEST_FAILED:
        return _pytest_spec(wf_payload, has_archive, has_repair_package)
    if code == ErrorCode.GOLDEN_OUTPUT_COMPARISON_FAILED:
        return _golden_spec(what_we_found, has_archive)
    if code in _VALIDATION_CODES:
        return _validation_spec(code, wf_payload, has_archive)
    if code in _PROVENANCE_CODES:
        return _provenance_spec(code, has_archive)
    if code == ErrorCode.REPAIR_CANNOT_REPRODUCE:
        return _repair_cannot_reproduce_spec(what_we_found, has_archive)
    if code in {ErrorCode.TEST_FAILED, ErrorCode.VALIDATION_LOOP_EXHAUSTED}:
        return _repair_post_patch_spec(has_archive)
    if code in _BUDGET_CODES or status == SessionStatus.FAILED_BUDGET:
        return _budget_spec(code)
    if code in {ErrorCode.SANDBOX_CRASH, ErrorCode.COMMAND_TIMEOUT} or status == SessionStatus.FAILED_SANDBOX:
        return _sandbox_spec(has_archive)
    if status == SessionStatus.FAILED_MODEL:
        return _model_spec()
    if code == ErrorCode.APPROVAL_DECLINED or status == SessionStatus.FAILED_USER_REJECT:
        return _approval_declined_spec()
    if code == ErrorCode.USER_ABANDONED:
        return _user_abandoned_spec()
    return _generic_spec(workflow, code, has_archive)


def _upload_spec(code: ErrorCode) -> dict[str, Any]:
    title = "We couldn't read that file"
    if code == ErrorCode.UNSUPPORTED_FILE_TYPE:
        title = "Unsupported file type"
    elif code == ErrorCode.FILE_TOO_LARGE:
        title = "File too large"
    return {
        "title": title,
        "summary": (
            "The upload could not be processed. Check that the file is a valid "
            "CSV or Excel workbook and within the size limit."
        ),
        "actions": [
            MitigationAction.UPLOAD_REPLACEMENT_FILE,
            MitigationAction.START_NEW_SESSION,
        ],
        "primary": MitigationAction.UPLOAD_REPLACEMENT_FILE,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "upload",
    }


def _schema_spec(code: ErrorCode) -> dict[str, Any]:
    if code == ErrorCode.AUTHOR_INTENT_SCHEMA_MISMATCH:
        return {
            "title": "Workflow and input do not match",
            "summary": (
                "The uploaded file columns do not align with the workflow you "
                "described, so no agent output was generated."
            ),
            "actions": [
                MitigationAction.UPLOAD_REPLACEMENT_FILE,
                MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
                MitigationAction.START_NEW_SESSION,
            ],
            "primary": MitigationAction.UPLOAD_REPLACEMENT_FILE,
            "retry_safe": True,
            "technical_ref": "events",
            "category": "schema",
        }
    return {
        "title": "Your file needs a different shape for this workflow",
        "summary": (
            "We could not map the uploaded columns to this workflow. "
            "Fix the sample file or choose a matching workflow."
        ),
        "actions": [
            MitigationAction.UPLOAD_REPLACEMENT_FILE,
            MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
            MitigationAction.START_NEW_SESSION,
        ],
        "primary": MitigationAction.UPLOAD_REPLACEMENT_FILE,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "schema",
    }


def _clarification_spec() -> dict[str, Any]:
    return {
        "title": "Quick question before we continue",
        "summary": "We need one clarification about your file before building the agent.",
        "actions": [
            MitigationAction.ANSWER_CLARIFICATION,
            MitigationAction.START_NEW_SESSION,
        ],
        "primary": MitigationAction.ANSWER_CLARIFICATION,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "clarification",
    }


def _contract_spec(code: ErrorCode, has_archive: bool) -> dict[str, Any]:
    actions = [
        MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        MitigationAction.PROVIDE_EXPECTED_OUTPUT,
        MitigationAction.START_NEW_SESSION,
    ]
    if has_archive:
        actions.insert(1, MitigationAction.DOWNLOAD_AUDIT_PACKAGE)
    return {
        "title": "We could not finalize the workflow plan",
        "summary": (
            "The agent's plan did not pass required planning checks. "
            "No validated output was produced."
        ),
        "actions": actions + [MitigationAction.OPEN_TECHNICAL_DETAILS],
        "primary": MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        "retry_safe": True,
        "technical_ref": "validation_report",
        "category": "model_contract",
    }


def _safety_spec(what_we_found: str | None, has_archive: bool) -> dict[str, Any]:
    actions = [
        MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        MitigationAction.UPLOAD_REPLACEMENT_FILE,
        MitigationAction.DOWNLOAD_AUDIT_PACKAGE if has_archive else MitigationAction.OPEN_TECHNICAL_DETAILS,
        MitigationAction.START_NEW_SESSION,
    ]
    return {
        "title": "The generated agent did not pass safety checks",
        "summary": (
            "A safety validation check blocked completion. Adjust the workflow "
            "description or sample file — do not bypass safety checks."
        ),
        "actions": actions,
        "primary": MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        "retry_safe": True,
        "technical_ref": "validation_report",
        "category": "codegen",
    }


def _codegen_spec(
    code: ErrorCode,
    workflow: Workflow,
    has_archive: bool,
    has_repair_package: bool,
) -> dict[str, Any]:
    title = "The generated agent did not run successfully"
    if code == ErrorCode.AUTHOR_CODE_GENERATION_FAILED:
        title = "We could not finish building the agent code"
    elif code == ErrorCode.AUTHOR_TEST_GENERATION_FAILED:
        title = "We could not finish writing the automated checks"
    elif code == ErrorCode.SAFETY_VALIDATION_FAILED:
        return _safety_spec(None, has_archive)

    actions: list[MitigationAction] = [
        MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        MitigationAction.RETRY_SAME_INPUTS,
    ]
    if has_archive:
        actions.append(MitigationAction.DOWNLOAD_AUDIT_PACKAGE)
    if has_repair_package and workflow == Workflow.AUTHOR:
        actions.append(MitigationAction.OPEN_REPAIR)
    actions.extend(
        [
            MitigationAction.OPEN_TECHNICAL_DETAILS,
            MitigationAction.START_NEW_SESSION,
        ]
    )
    return {
        "title": title,
        "summary": (
            "The system stopped while generating or running the agent. "
            "Review the technical details and adjust your inputs."
        ),
        "actions": actions,
        "primary": MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "codegen",
    }


def _pytest_spec(
    wf_payload: dict[str, Any] | None,
    has_archive: bool,
    has_repair_package: bool,
) -> dict[str, Any]:
    actions: list[MitigationAction] = [
        MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        MitigationAction.PROVIDE_EXPECTED_OUTPUT,
    ]
    if has_archive:
        actions.append(MitigationAction.DOWNLOAD_AUDIT_PACKAGE)
    if has_repair_package:
        actions.append(MitigationAction.OPEN_REPAIR)
    actions.extend(
        [
            MitigationAction.RETRY_SAME_INPUTS,
            MitigationAction.OPEN_TECHNICAL_DETAILS,
            MitigationAction.START_NEW_SESSION,
        ]
    )
    return {
        "title": "Output did not pass quality checks",
        "summary": (
            "The workflow was built, but one or more automated quality checks "
            "did not pass before the session could finish."
        ),
        "actions": actions,
        "primary": MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        "retry_safe": True,
        "technical_ref": "validation_report",
        "category": "pytest",
    }


def _golden_spec(what_we_found: str | None, has_archive: bool) -> dict[str, Any]:
    actions = [
        MitigationAction.PROVIDE_EXPECTED_OUTPUT,
        MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        MitigationAction.DOWNLOAD_AUDIT_PACKAGE if has_archive else MitigationAction.OPEN_TECHNICAL_DETAILS,
        MitigationAction.START_NEW_SESSION,
    ]
    return {
        "title": "Output did not match expected review standards",
        "summary": (
            "The processed results did not match the independent quality check. "
            "Review the output and adjust your workflow rules or sample file."
        ),
        "actions": actions,
        "primary": MitigationAction.PROVIDE_EXPECTED_OUTPUT,
        "retry_safe": True,
        "technical_ref": "validation_report",
        "category": "golden",
    }


def _validation_spec(
    code: ErrorCode,
    wf_payload: dict[str, Any] | None,
    has_archive: bool,
) -> dict[str, Any]:
    title = "Output did not pass validation"
    if code == ErrorCode.UNIVERSAL_VALIDATION_FAILED:
        title = "Basic output checks did not pass"
    elif code == ErrorCode.CONTRACT_SPECIFIC_VALIDATION_FAILED:
        title = "Workflow rule checks did not pass"
    elif code == ErrorCode.AUTHOR_CUSTOM_BUILD_FAILED:
        title = "Could not complete custom workflow build"

    actions = [
        MitigationAction.DOWNLOAD_AUDIT_PACKAGE if has_archive else MitigationAction.OPEN_TECHNICAL_DETAILS,
        MitigationAction.EDIT_WORKFLOW_DESCRIPTION,
        MitigationAction.PROVIDE_EXPECTED_OUTPUT,
        MitigationAction.RETRY_SAME_INPUTS,
        MitigationAction.START_NEW_SESSION,
    ]
    return {
        "title": title,
        "summary": (
            "The agent produced output, but one or more validation checks did not pass. "
            "Download the validation summary to see which check failed."
        ),
        "actions": actions,
        "primary": MitigationAction.DOWNLOAD_AUDIT_PACKAGE
        if has_archive
        else MitigationAction.OPEN_TECHNICAL_DETAILS,
        "retry_safe": True,
        "technical_ref": "validation_report",
        "category": "validation",
    }


def _provenance_spec(code: ErrorCode, has_archive: bool) -> dict[str, Any]:
    actions: list[MitigationAction] = [
        MitigationAction.RETRY_SAME_INPUTS,
    ]
    if has_archive:
        actions.append(MitigationAction.DOWNLOAD_AUDIT_PACKAGE)
    actions.extend(
        [
            MitigationAction.OPEN_TECHNICAL_DETAILS,
            MitigationAction.START_NEW_SESSION,
        ]
    )

    return {
        "title": "Workflow could not be finalized",
        "summary": (
            "The agent produced outputs that passed validation checks, but the "
            "session could not be finalized. Your validation summary and processed "
            "results were preserved for review."
        ),
        "actions": actions,
        "primary": MitigationAction.RETRY_SAME_INPUTS,
        "retry_safe": True,
        "technical_ref": "validation_report",
        "category": "provenance",
    }


def _repair_cannot_reproduce_spec(
    what_we_found: str | None,
    has_archive: bool,
) -> dict[str, Any]:
    return {
        "title": "Could not reproduce the reported issue",
        "summary": (
            "Pre-fix checks did not match the problem you reported. "
            "No patch was applied."
        ),
        "actions": [
            MitigationAction.UPLOAD_PROBLEM_REPORT,
            MitigationAction.UPLOAD_REPLACEMENT_FILE,
            MitigationAction.DOWNLOAD_AUDIT_PACKAGE if has_archive else MitigationAction.OPEN_TECHNICAL_DETAILS,
            MitigationAction.START_NEW_SESSION,
        ],
        "primary": MitigationAction.UPLOAD_PROBLEM_REPORT,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "repair",
    }


def _repair_post_patch_spec(has_archive: bool) -> dict[str, Any]:
    return {
        "title": "Checks still failing after the fix",
        "summary": (
            "A patch was attempted but post-fix automated checks did not pass. "
            "Review the repair report and evidence before retrying."
        ),
        "actions": [
            MitigationAction.DOWNLOAD_AUDIT_PACKAGE if has_archive else MitigationAction.OPEN_TECHNICAL_DETAILS,
            MitigationAction.UPLOAD_PROBLEM_REPORT,
            MitigationAction.START_NEW_SESSION,
        ],
        "primary": MitigationAction.DOWNLOAD_AUDIT_PACKAGE
        if has_archive
        else MitigationAction.OPEN_TECHNICAL_DETAILS,
        "retry_safe": False,
        "technical_ref": "validation_report",
        "category": "repair",
    }


def _budget_spec(code: ErrorCode | None) -> dict[str, Any]:
    title = "Session budget reached"
    if code == ErrorCode.BUDGET_EXHAUSTED_WALL_TIME:
        title = "Session time limit reached"
    elif code == ErrorCode.BUDGET_EXHAUSTED_STEPS:
        title = "Session step budget reached"
    return {
        "title": title,
        "summary": (
            "The session hit a budget limit before finishing. "
            "Progress saved: your outputs and session record were preserved for review."
        ),
        "actions": [
            MitigationAction.SIMPLIFY_WORKFLOW,
            MitigationAction.UPLOAD_REPLACEMENT_FILE,
            MitigationAction.INCREASE_BUDGET,
            MitigationAction.DOWNLOAD_AUDIT_PACKAGE,
            MitigationAction.START_NEW_SESSION,
        ],
        "primary": MitigationAction.SIMPLIFY_WORKFLOW,
        "retry_safe": True,
        "technical_ref": "manifest",
        "category": "budget",
    }


def _sandbox_spec(has_archive: bool) -> dict[str, Any]:
    return {
        "title": "An unexpected execution error occurred",
        "summary": (
            "The isolated run environment stopped before finishing. "
            "Technical logs were preserved in the audit package."
        ),
        "actions": [
            MitigationAction.DOWNLOAD_AUDIT_PACKAGE if has_archive else MitigationAction.OPEN_TECHNICAL_DETAILS,
            MitigationAction.UPLOAD_REPLACEMENT_FILE,
            MitigationAction.RETRY_SAME_INPUTS,
            MitigationAction.START_NEW_SESSION,
        ],
        "primary": MitigationAction.DOWNLOAD_AUDIT_PACKAGE
        if has_archive
        else MitigationAction.OPEN_TECHNICAL_DETAILS,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "sandbox",
    }


def _model_spec() -> dict[str, Any]:
    return {
        "title": "The AI service returned an error",
        "summary": (
            "The model provider failed before the workflow could finish. "
            "This is usually transient."
        ),
        "actions": [
            MitigationAction.RETRY_SAME_INPUTS,
            MitigationAction.START_NEW_SESSION,
            MitigationAction.OPEN_TECHNICAL_DETAILS,
        ],
        "primary": MitigationAction.RETRY_SAME_INPUTS,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "model",
    }


def _approval_declined_spec() -> dict[str, Any]:
    return {
        "title": "Session stopped because approval was declined",
        "summary": "A required approval step was declined, so the workflow did not continue.",
        "actions": [
            MitigationAction.START_NEW_SESSION,
            MitigationAction.OPEN_TECHNICAL_DETAILS,
        ],
        "primary": MitigationAction.START_NEW_SESSION,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "approval",
    }


def _user_abandoned_spec() -> dict[str, Any]:
    return {
        "title": "Session cancelled",
        "summary": "You stopped this session before it finished.",
        "actions": [MitigationAction.START_NEW_SESSION],
        "primary": MitigationAction.START_NEW_SESSION,
        "retry_safe": True,
        "technical_ref": "events",
        "category": "user",
    }


def _generic_spec(
    workflow: Workflow,
    code: ErrorCode | None,
    has_archive: bool,
) -> dict[str, Any]:
    label = workflow.value
    return {
        "title": "Session stopped",
        "summary": f"The {label} workflow stopped before finishing.",
        "actions": [
            MitigationAction.DOWNLOAD_AUDIT_PACKAGE if has_archive else MitigationAction.OPEN_TECHNICAL_DETAILS,
            MitigationAction.START_NEW_SESSION,
        ],
        "primary": MitigationAction.START_NEW_SESSION,
        "retry_safe": code != ErrorCode.UNKNOWN,
        "technical_ref": "events",
        "category": "unknown",
    }


def _latest_workflow_failed_payload(
    events: list[WorkspaceEvent],
) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.kind != EventKind.WORKFLOW_FAILED:
            continue
        payload = event.payload
        if isinstance(payload, dict):
            return payload
    return None


def _budget_code_from_events(events: list[WorkspaceEvent]) -> ErrorCode | None:
    for event in reversed(events):
        if event.kind != EventKind.BUDGET_EXHAUSTED:
            continue
        raw = event.payload.get("error_code")
        if isinstance(raw, str):
            try:
                return ErrorCode(raw)
            except ValueError:
                pass
        kind = event.payload.get("kind")
        mapping = {
            "tokens": ErrorCode.BUDGET_EXHAUSTED_TOKENS,
            "tool_calls": ErrorCode.BUDGET_EXHAUSTED_TOOL_CALLS,
            "steps": ErrorCode.BUDGET_EXHAUSTED_STEPS,
            "wall_time": ErrorCode.BUDGET_EXHAUSTED_WALL_TIME,
            "file_count": ErrorCode.BUDGET_EXHAUSTED_FILE_COUNT,
        }
        if isinstance(kind, str) and kind in mapping:
            return mapping[kind]
    return None


def _collect_evidence_items(
    events: list[WorkspaceEvent],
    workspace: Path | None,
) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()

    if workspace is not None:
        for rel, label in _EVIDENCE_FILE_LABELS:
            if (workspace / rel).is_file() and label not in seen:
                seen.add(label)
                items.append(label)

    return items


def _humanize_what_we_found(
    raw: str | None,
    code: ErrorCode | None,
) -> str | None:
    if code in _PROVENANCE_CODES:
        return "The workflow plan file was changed after it was approved."
    if code == ErrorCode.GOLDEN_OUTPUT_COMPARISON_FAILED:
        return "Some rows did not match the independent quality check."
    if code == ErrorCode.GENERATED_PYTEST_FAILED:
        return "One or more automated quality checks did not pass."

    if not raw or not raw.strip():
        return None

    lowered = raw.lower()
    if any(term in lowered for term in _FINANCE_FORBIDDEN_TERMS):
        if code in _VALIDATION_CODES:
            return "One or more validation checks did not pass."
        return None

    if _looks_technical(lowered):
        if "golden" in lowered or "review_required" in lowered:
            return "Some rows did not match the independent quality check."
        if re.search(r"\d+\s+failed", lowered):
            return "One or more automated quality checks did not pass."
        return None

    return raw.strip()


def _looks_technical(text: str) -> bool:
    if "/" in text or "\\" in text:
        return True
    if text.endswith(".md") or text.endswith(".csv") or text.endswith(".json"):
        return True
    if "`" in text:
        return True
    technical_markers = (
        "traceback",
        "subprocess",
        "error_code",
        "failed_layer",
        "pytest_summary",
        "generated/",
        "reports/",
        "outputs/",
    )
    return any(marker in text for marker in technical_markers)


def _extract_what_we_found(
    wf_payload: dict[str, Any] | None,
    events: list[WorkspaceEvent],
) -> str | None:
    if wf_payload:
        discovered = wf_payload.get("discovered_issue_summary")
        if isinstance(discovered, str) and discovered.strip():
            return discovered.strip()
        failed_check = wf_payload.get("failed_check")
        if isinstance(failed_check, str) and failed_check.strip():
            return failed_check.strip()
        message = wf_payload.get("message")
        if isinstance(message, str) and message.strip() and len(message) < 300:
            if "traceback" not in message.lower():
                return message.strip()
    for event in reversed(events):
        if event.kind != EventKind.WORKFLOW_FAILED:
            continue
        summary = event.payload.get("discovered_issue_summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return None


def _workspace_has_archive(workspace: Path | None) -> bool:
    if workspace is None:
        return False
    return (workspace / "archive.zip").is_file()


def _workspace_has_repair_package(workspace: Path | None) -> bool:
    if workspace is None:
        return False
    generated_agent = workspace / "generated" / "agent.py"
    generated_tests = workspace / "generated" / "tests"
    working_agent = workspace / "working" / "agent.py"
    working_tests = workspace / "working" / "tests"
    return (
        (generated_agent.is_file() and generated_tests.is_dir())
        or (working_agent.is_file() and working_tests.is_dir())
    )


__all__ = [
    "FailureMitigation",
    "MitigationAction",
    "build_failure_mitigation",
]
