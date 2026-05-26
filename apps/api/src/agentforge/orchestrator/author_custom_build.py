"""LLM-first Author workflow build pipeline.

State sequence (see docs/author-workflow.md):
  profile → optional date pause → model authoring (contract/review/code/tests)
  → safety scan → execute → pytest → four-tier validation → archive.

Failure paths: unreadable upload (terminal), clarification (paused_user),
authoring failure (terminal), repair loop in _execute_contract_build_tail
(bounded by settings.author_max_repair_attempts), validation tier failure
(terminal with typed error_code).

Boundary: this module orchestrates; it does not dispatch tools directly.
Model output is parsed in author_llm_authoring.py; validation runs in
validation/ and author_contract_validation.py. INV-1, INV-12.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from agentforge.config import Settings
from agentforge.orchestrator.author_contract import (
    CONTRACT_REL,
    profile_tabular_file,
)
from agentforge.orchestrator.author_date_clarification import (
    apply_date_format_clarification_gate,
    clarification_to_payload,
    find_resolved_date_clarification,
)
from agentforge.orchestrator.author_contract_validation import (
    production_outputs_need_agent_restore,
    validate_against_contract,
)
from agentforge.orchestrator.author_llm_authoring import (
    _is_expense_exception_review_context,
    AI_AUTHORED_WORKFLOW_BUILD_VIA,
    _DICTWRITER_FIELDNAMES_VALUEERROR_RE,
    _BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES,
    _BUNDLED_BANK_REFERENCE_EXPECTED_CATEGORY_COUNTS,
    _BUNDLED_BANK_REFERENCE_GOLDEN_WORKSPACE_REL,
    _BUNDLED_BANK_REFERENCE_ROW_EXPECTATIONS,
    _BUNDLED_BANK_REFERENCE_TEMPLATE,
    _BUNDLED_BANK_REFERENCE_WARNING,
    _bundled_bank_reference_contract_scaffold,
    _is_bundled_bank_reference_contract,
    _is_bundled_bank_reference_sample,
    apply_bundled_bank_reference_golden_policy,
    apply_expense_exception_golden_policy,
    bank_categoriser_template_root,
    stage_bundled_bank_reference_golden,
    stage_expense_exception_golden,
    sync_author_output_contract_provenance,
    _parse_dictwriter_extra_fieldnames,
    finalize_repair_candidate_promotion,
    generated_agent_cli_args,
    generated_agent_cli_command,
    promote_repair_candidate_files,
    refresh_model_authoring_summary,
    repair_generated_author_files,
    run_model_authoring_pipeline,
)
from agentforge.orchestrator.tool_scope_audit import record_tool_action
from agentforge.orchestrator.workflow_artifacts import (
    artifact_type_for_required_path,
    choose_system_validation_report_path,
    normalize_artifact_type,
    snapshot_required_workflow_artifacts,
    system_validation_report_json_path,
    verify_workflow_artifacts_preserved,
    workflow_report_path_from_contract,
)
from agentforge.persistence.archive import build_archive
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.user_facing import (
    sanitize_user_facing_workflow_reports,
    sync_manifest_budget_from_events,
)
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.sandbox import SandboxRunner
from agentforge.schemas import (
    ActorType,
    ArtifactSummary,
    ArtifactType,
    CompletionFailureMetadata,
    CompletionMetadata,
    ErrorCode,
    EventKind,
    SessionStatus,
    TestResults,
    ValidationCheck,
    ValidationLayer,
    ValidationLayerSummary,
    ValidationReport,
)
from agentforge.schemas.author_output_contract import (
    AuthorOutputContract,
    TabularFileProfile,
    deliverable_label,
    deliverable_output_path,
    deliverable_required,
)
from agentforge.tools.validation_tools import (
    GENERATED_PYTEST_MIN_COLLECTED,
    _parse_pytest_minimal,
    effective_collected_test_count,
    generated_pytest_gate_failed,
)
from agentforge.validation import (
    classify_validation_check,
    render_json,
    render_markdown,
    validation_failure_error_code,
)

CUSTOM_WORKFLOW_BUILD_VIA = "ai_authored_workflow_build"

_logger = logging.getLogger("agentforge.orchestrator.author_custom_build")


@dataclass(frozen=True)
class UploadedDataFile:
    path: Path
    format: str  # csv | xlsx


@dataclass(frozen=True)
class CustomIngestResult:
    original_upload_path: str
    upload_format: str
    selected_sheet: str | None
    normalized_input_path: str
    agent_input_path: str
    columns: list[str]
    row_count: int


@dataclass(frozen=True)
class AgentSyntaxPreflight:
    ok: bool
    filename: str
    absolute_path: str
    error_type: str | None = None
    message: str | None = None
    line_number: int | None = None
    offset: int | None = None
    failing_line: str | None = None
    nearby_code_excerpt: str | None = None
    technical_detail: str | None = None


def _profile_to_ingest(profile: TabularFileProfile) -> CustomIngestResult:
    return CustomIngestResult(
        original_upload_path=profile.original_upload_path,
        upload_format=profile.input_format,
        selected_sheet=profile.selected_sheet,
        normalized_input_path=profile.normalized_input_path,
        agent_input_path=profile.agent_input_path,
        columns=list(profile.columns),
        row_count=profile.row_count,
    )


def _latest_template_hint(event_log: EventLog, session_id: UUID) -> str | None:
    for event in reversed(event_log.read_all(session_id)):
        if event.kind != EventKind.DECISION_INPUT:
            continue
        payload = event.payload or {}
        if payload.get("kind") != "template_hint":
            continue
        value = payload.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _truthy_demo_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _bank_reference_sample_observations(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
) -> dict[str, Any]:
    output_path = workspace / contract.row_level_output_file
    if not output_path.is_file():
        return {"rows": [], "category_counts": {}, "mismatches": [], "missing_columns": []}

    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    expected_columns = set(contract.output_columns)
    actual_columns = set(rows[0].keys()) if rows else set()
    missing_columns = sorted(expected_columns - actual_columns)

    allowed = set(_BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES)
    by_id = {str(row.get("transaction_id") or "").strip(): row for row in rows}
    category_counts: dict[str, int] = {}
    mismatches: list[dict[str, Any]] = []

    for row in rows:
        category = str(row.get("category") or "").strip()
        category_counts[category] = category_counts.get(category, 0) + 1
        if category not in allowed:
            mismatches.append(
                {
                    "transaction_id": row.get("transaction_id"),
                    "issue": "invalid_category",
                    "actual_category": category,
                }
            )

    for item in _BUNDLED_BANK_REFERENCE_ROW_EXPECTATIONS:
        row = by_id.get(item["transaction_id"])
        if row is None:
            mismatches.append(
                {
                    "transaction_id": item["transaction_id"],
                    "issue": "missing_row",
                    "expected_category": item["expected_category"],
                }
            )
            continue

        actual_category = str(row.get("category") or "").strip()
        if actual_category != item["expected_category"]:
            mismatches.append(
                {
                    "transaction_id": item["transaction_id"],
                    "description": item["description"],
                    "issue": "wrong_category",
                    "expected_category": item["expected_category"],
                    "actual_category": actual_category,
                    "rule_matched": row.get("rule_matched"),
                    "rule_used": row.get("rule_used"),
                }
            )

        if item["expected_category"] == "Other":
            try:
                confidence = float(str(row.get("confidence_score") or "").strip())
            except ValueError:
                confidence = 1.0
            review_required = _truthy_demo_flag(row.get("review_required"))
            if confidence >= 0.70:
                mismatches.append(
                    {
                        "transaction_id": item["transaction_id"],
                        "description": item["description"],
                        "issue": "other_confidence_not_low",
                        "actual_confidence_score": row.get("confidence_score"),
                    }
                )
            if not review_required:
                mismatches.append(
                    {
                        "transaction_id": item["transaction_id"],
                        "description": item["description"],
                        "issue": "other_review_required_false",
                        "actual_review_required": row.get("review_required"),
                    }
                )

    return {
        "rows": rows,
        "category_counts": category_counts,
        "mismatches": mismatches,
        "missing_columns": missing_columns,
    }


def _bank_reference_sample_validation_checks(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
) -> list[ValidationCheck]:
    if contract.row_level_output_file != "outputs/output.csv":
        return []
    output_path = workspace / contract.row_level_output_file
    report_path = workspace / "reports" / "validation_report.md"
    if not output_path.is_file():
        return []
    observations = _bank_reference_sample_observations(
        workspace=workspace,
        contract=contract,
    )
    rows = observations["rows"]
    missing_columns = observations["missing_columns"]
    category_counts = observations["category_counts"]
    mismatches = observations["mismatches"]

    checks: list[ValidationCheck] = []
    checks.append(
        ValidationCheck(
            layer=ValidationLayer.SEMANTIC_RULES,
            name="Bundled bank demo output shape",
            passed=(
                len(rows) == 18
                and not missing_columns
            ),
            evidence=(
                f"rows={len(rows)}; missing_columns={missing_columns}"
                if len(rows) != 18 or missing_columns
                else "18 data rows with expected output columns"
            ),
        )
    )

    category_violations = [
        (
            f"{item['transaction_id']}: expected {item['expected_category']!r} got "
            f"{item['actual_category']!r}"
            if item["issue"] == "wrong_category"
            else f"{item['transaction_id']}: category={item['actual_category']!r}"
            if item["issue"] == "invalid_category"
            else f"{item['transaction_id']}: missing row"
            if item["issue"] == "missing_row"
            else f"{item['transaction_id']}: Other row confidence_score must be below 0.70"
            if item["issue"] == "other_confidence_not_low"
            else f"{item['transaction_id']}: Other row review_required must be true"
        )
        for item in mismatches
    ]
    checks.append(
        ValidationCheck(
            layer=ValidationLayer.SEMANTIC_RULES,
            name="Bundled bank demo expected classifications",
            passed=not category_violations,
            evidence=(
                (
                    "; ".join(category_violations[:8])
                    + f"; counts={category_counts}"
                )
                if category_violations
                else (
                    "all 18 bundled demo row expectations satisfied; "
                    f"counts={category_counts}"
                )
            ),
        )
    )

    report_text = (
        report_path.read_text(encoding="utf-8", errors="ignore").strip()
        if report_path.is_file()
        else ""
    )
    checks.append(
        ValidationCheck(
            layer=ValidationLayer.SEMANTIC_RULES,
            name="Bundled bank demo report is non-empty",
            passed=bool(report_text),
            evidence=(
                "validation_report.md is non-empty"
                if report_text
                else "reports/validation_report.md missing or empty"
            ),
        )
    )
    return checks



async def execute_custom_workflow_pipeline(
    *,
    session_id: UUID,
    workspace: Path,
    upload: UploadedDataFile,
    workflow_type: str | None,
    user_description: str,
    settings: Settings,
    event_log: EventLog,
    workspace_manager: WorkspaceManager,
    step: int,
    stage_label: str = CUSTOM_WORKFLOW_BUILD_VIA,
    model_client: Any | None = None,
) -> tuple[SessionStatus, ErrorCode | None]:
    template_hint = _latest_template_hint(event_log, session_id)
    author_phase = "author.build"
    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name="inspect_file",
        workflow="author",
        phase=author_phase,
        controlled_by="backend",
        dispatch_mode="orchestrator",
        status="started",
        inputs=[upload.path],
    )
    try:
        profile = profile_tabular_file(
            workspace=workspace,
            upload_path=upload.path,
            upload_format=upload.format,
        )
        ingest = _profile_to_ingest(profile)
    except (OSError, ValueError) as exc:
        record_tool_action(
            event_log=event_log,
            session_id=session_id,
            step=step,
            tool_name="inspect_file",
            workflow="author",
            phase=author_phase,
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="failed",
            inputs=[upload.path],
            detail=str(exc),
        )
        _fail(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            error_code=ErrorCode.AUTHOR_CUSTOM_BUILD_FAILED,
            message=f"Could not read uploaded file: {exc}",
        )
        return (SessionStatus.FAILED_OTHER, ErrorCode.AUTHOR_CUSTOM_BUILD_FAILED)

    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name="inspect_csv_schema",
        workflow="author",
        phase=author_phase,
        controlled_by="backend",
        dispatch_mode="orchestrator",
        status="completed",
        inputs=[upload.path],
        outputs=[
            ingest.normalized_input_path,
            ingest.agent_input_path,
        ],
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "custom_workflow_ingest",
            "stage": stage_label,
            "original_upload_path": ingest.original_upload_path,
            "upload_format": ingest.upload_format,
            "selected_sheet": ingest.selected_sheet,
            "normalized_input_path": ingest.normalized_input_path,
            "columns": ingest.columns,
            "row_count": ingest.row_count,
            "column_profiles": [p.model_dump(mode="json") for p in profile.column_profiles],
            "candidate_id_columns": profile.candidate_id_columns,
            "candidate_grouping_columns": profile.candidate_grouping_columns,
            "sample_rows": profile.sample_rows[:3],
        },
        step=step,
    )

    clarification_pause = apply_date_format_clarification_gate(
        session_id=session_id,
        event_log=event_log,
        upload_path=upload.path,
        columns=ingest.columns,
        step=step,
        stage_label=stage_label,
    )
    if clarification_pause is not None:
        return (clarification_pause, None)

    if model_client is None:
        _fail(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            error_code=ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING,
            message=(
                "Authoring requires model analysis of the uploaded file and workflow "
                "request, but no model client was available."
            ),
        )
        return (
            SessionStatus.FAILED_OTHER,
            ErrorCode.AUTHOR_MODEL_REQUIRED_FOR_AUTHORING,
        )

    schema_profile = {
        "columns": ingest.columns,
        "row_count": ingest.row_count,
        "upload_format": ingest.upload_format,
        "selected_sheet": ingest.selected_sheet,
        "original_upload_path": ingest.original_upload_path,
        "normalized_input_path": ingest.normalized_input_path,
        "agent_input_path": ingest.agent_input_path,
        "sample_rows": profile.sample_rows[:5],
        "column_profiles": [p.model_dump(mode="json") for p in profile.column_profiles],
        "candidate_id_columns": profile.candidate_id_columns,
        "candidate_date_columns": profile.candidate_date_columns,
        "candidate_amount_columns": profile.candidate_amount_columns,
        "candidate_status_columns": profile.candidate_status_columns,
        "candidate_category_columns": profile.candidate_category_columns,
        "candidate_grouping_columns": profile.candidate_grouping_columns,
        "candidate_reference_columns": profile.candidate_reference_columns,
        "null_profiles": [p.model_dump(mode="json") for p in profile.null_profiles],
        "uniqueness_profiles": [
            p.model_dump(mode="json") for p in profile.uniqueness_profiles
        ],
        "workbook_sheets": [s.model_dump(mode="json") for s in profile.workbook_sheets],
    }
    resolved_clarification = find_resolved_date_clarification(
        event_log.read_all(session_id)
    )
    if resolved_clarification is not None:
        schema_profile["date_format_clarification"] = clarification_to_payload(
            resolved_clarification
        )
    bundled_bank_reference = _is_bundled_bank_reference_sample(
        template_hint=template_hint,
        schema_profile=schema_profile,
    )
    bundled_bank_reference_mode = (
        settings.author_enable_bank_reference_scaffold and bundled_bank_reference
    )
    output_contract_hint = (
        _bundled_bank_reference_contract_scaffold(
            schema_profile=schema_profile
        ).model_dump(mode="json")
        if bundled_bank_reference_mode
        else None
    )
    if bundled_bank_reference_mode:
        golden_staged = stage_bundled_bank_reference_golden(workspace=workspace)
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "reference_sample_scaffold_mode",
                "stage": stage_label,
                "template": _BUNDLED_BANK_REFERENCE_TEMPLATE,
                "message": _BUNDLED_BANK_REFERENCE_WARNING,
                "contract_seed_path": "in_memory",
                "golden_staged": golden_staged,
                "golden_output_path": _BUNDLED_BANK_REFERENCE_GOLDEN_WORKSPACE_REL
                if golden_staged
                else None,
            },
            step=step,
        )
    authoring = await run_model_authoring_pipeline(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=AI_AUTHORED_WORKFLOW_BUILD_VIA,
        model_client=model_client,
        workspace=workspace,
        user_description=user_description,
        schema_profile=schema_profile,
        reference_scaffold_root=None,
        workflow_type=workflow_type or "model_authored_finance_workflow",
        template_hint=template_hint,
        output_contract_hint=output_contract_hint,
        settings=settings,
    )
    if not authoring.ok:
        if authoring.error_code == ErrorCode.AUTHOR_CONTRACT_CLARIFICATION_REQUIRED:
            event_log.append(
                session_id=session_id,
                kind=EventKind.QUESTION_ASKED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "question_event_id": str(uuid4()),
                    "plain_english_question": authoring.message
                    or "Please clarify the workflow before authoring can continue.",
                    "technical_context": authoring.technical_detail or "",
                },
                step=step,
            )
            return (SessionStatus.PAUSED_USER, None)
        _fail(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            error_code=authoring.error_code or ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE,
            message=authoring.message or "Model authoring did not produce runnable agent files.",
            technical_detail=authoring.technical_detail,
        )
        return (
            SessionStatus.FAILED_OTHER,
            authoring.error_code or ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE,
        )

    if authoring.output_contract is None:
        _fail(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            error_code=ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED,
            message="Model authoring completed without a model-authored AuthorOutputContract.",
        )
        return (
            SessionStatus.FAILED_OTHER,
            ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED,
        )

    model_contract = authoring.output_contract
    if bundled_bank_reference_mode and _is_bundled_bank_reference_contract(
        template_hint=template_hint,
        contract=model_contract,
    ):
        stage_bundled_bank_reference_golden(workspace=workspace)
        model_contract = apply_bundled_bank_reference_golden_policy(model_contract)
        sync_author_output_contract_provenance(
            session_id=session_id,
            event_log=event_log,
            workspace=workspace,
            contract=model_contract,
            step=step,
            stage_label=stage_label,
        )
    elif _is_expense_exception_review_context(
        template_hint=template_hint,
        schema_profile=schema_profile,
    ):
        if stage_expense_exception_golden(workspace=workspace):
            model_contract = apply_expense_exception_golden_policy(model_contract)
            sync_author_output_contract_provenance(
                session_id=session_id,
                event_log=event_log,
                workspace=workspace,
                contract=model_contract,
                step=step,
                stage_label=stage_label,
            )
    if not (workspace / CONTRACT_REL).is_file():
        _fail(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            error_code=ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED,
            message="Model authoring did not persist generated/author_output_contract.json.",
        )
        return (
            SessionStatus.FAILED_OTHER,
            ErrorCode.AUTHOR_CONTRACT_PLANNING_FAILED,
        )
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "author_output_contract",
            "stage": stage_label,
            "source": "model_authored",
            "contract": model_contract.model_dump(mode="json"),
            "build_mode": model_contract.build_mode,
        },
        step=step,
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "custom_workflow_output_contract",
            "stage": stage_label,
            "source": "model_authored",
            "workflow_type": model_contract.workflow_type,
            "input_columns": model_contract.input_columns,
            "output_columns": model_contract.output_columns,
            "required_output_columns": model_contract.required_output_columns,
            "summary_output_files": model_contract.summary_output_files,
            "exception_output_files": model_contract.exception_output_files,
            "warnings": model_contract.warnings,
        },
        step=step,
    )

    if model_contract.clarification_questions:
        question = model_contract.clarification_questions[0]
        event_log.append(
            session_id=session_id,
            kind=EventKind.QUESTION_ASKED,
            actor_type=ActorType.SYSTEM,
            payload={
                "question_event_id": str(uuid4()),
                "plain_english_question": question.question,
                "technical_context": question.reason,
            },
            step=step,
        )
        return (SessionStatus.PAUSED_USER, None)

    if model_contract.unsupported_reasons:
        _fail(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            error_code=ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED,
            message=model_contract.unsupported_reasons[0],
        )
        return (
            SessionStatus.FAILED_OTHER,
            ErrorCode.AUTHOR_CONTRACT_REVIEW_FAILED,
        )

    return await _execute_contract_build_tail(
        session_id=session_id,
        workspace=workspace,
        ingest=ingest,
        contract=model_contract,
        workflow_type=model_contract.workflow_type or workflow_type,
        settings=settings,
        event_log=event_log,
        workspace_manager=workspace_manager,
        step=step,
        stage_label=stage_label,
        build_mode=model_contract.build_mode,
        completion_via=AI_AUTHORED_WORKFLOW_BUILD_VIA,
        authoring_provenance=authoring.provenance,
        model_client=model_client,
        user_description=user_description,
        schema_profile=schema_profile,
        template_hint=template_hint,
    )


async def _execute_contract_build_tail(
    *,
    session_id: UUID,
    workspace: Path,
    ingest: CustomIngestResult,
    contract: AuthorOutputContract,
    workflow_type: str,
    settings: Settings,
    event_log: EventLog,
    workspace_manager: WorkspaceManager,
    step: int,
    stage_label: str,
    build_mode: str,
    completion_via: str,
    authoring_provenance: dict[str, Any] | None = None,
    model_client: Any | None = None,
    user_description: str = "",
    schema_profile: dict[str, Any] | None = None,
    template_hint: str | None = None,
) -> tuple[SessionStatus, ErrorCode | None]:
    contract_rel = CONTRACT_REL
    agent_py = workspace / "generated" / "agent.py"
    output_abs = workspace / contract.row_level_output_file
    output_abs.parent.mkdir(parents=True, exist_ok=True)
    sandbox = SandboxRunner(settings=settings, workspace_manager=workspace_manager)
    schema_profile = schema_profile or {}
    max_repair_attempts = max(1, int(settings.author_max_repair_attempts))
    repair_attempt = 0
    # Bounded execution repair (INV-12): runtime/pytest/contract/safety failures
    # call _attempt_repair up to author_max_repair_attempts times before terminal fail.
    report: ValidationReport | None = None
    all_warnings: list[str] = []
    original_runtime_failure_detail: str | None = None
    original_pytest_failure_detail: str | None = None
    bundled_bank_reference_mode = (
        settings.author_enable_bank_reference_scaffold
        and _is_bundled_bank_reference_contract(
            template_hint=template_hint,
            contract=contract,
        )
    )

    async def _attempt_repair(
        *,
        failure_kind: str,
        failure_detail: str,
    ) -> tuple[bool, str]:
        nonlocal repair_attempt
        current_kind = failure_kind
        current_detail = failure_detail
        while repair_attempt < max_repair_attempts:
            repair_attempt += 1
            repair = await repair_generated_author_files(
                session_id=session_id,
                event_log=event_log,
                step=step,
                stage_label=stage_label,
                model_client=model_client,
                workspace=workspace,
                user_description=user_description,
                schema_profile=schema_profile,
                contract=contract,
                failure_kind=current_kind,
                failure_detail=current_detail,
                attempt=repair_attempt,
                template_hint=template_hint,
                settings=settings,
                defer_promotion=current_kind == "pytest",
            )
            if current_kind == "pytest" and repair.ok:
                candidate_paths = repair.repair_candidate_paths
                if not candidate_paths:
                    return False, (
                        "Generated pytest repair did not stage a candidate test file."
                    )
                candidate_target = candidate_paths[0]
                candidate_results, candidate_exit_code, candidate_output = _run_generated_pytest(
                    session_id=session_id,
                    workspace=workspace,
                    settings=settings,
                    workspace_manager=workspace_manager,
                    step=step,
                    stage_label=stage_label,
                    event_log=event_log,
                    tests_target=candidate_target,
                    event_kind="pytest_candidate_run",
                    candidate_attempt=repair_attempt,
                    provider=repair.repair_provider,
                    model=repair.repair_model,
                )
                if generated_pytest_gate_failed(candidate_results, candidate_exit_code):
                    latest_failure = _pytest_candidate_failure_detail(
                        test_results=candidate_results,
                        exit_code=candidate_exit_code,
                        output=candidate_output,
                        candidate_path=candidate_target,
                    )
                    combined = _combine_named_failure_details(
                        original_label="Original generated pytest failure before repair",
                        original_failure=failure_detail,
                        latest_label="Latest generated pytest repair candidate failure",
                        latest_failure=latest_failure,
                    )
                    event_log.append(
                        session_id=session_id,
                        kind=EventKind.DECISION_INPUT,
                        actor_type=ActorType.SYSTEM,
                        payload={
                            "kind": "repair_candidate_rejected",
                            "stage": stage_label,
                            "attempt": repair_attempt,
                            "failure_kind": "pytest",
                            "candidate_paths": candidate_paths,
                            "provider": repair.repair_provider or "unknown",
                            "model": repair.repair_model or "unknown",
                            "pytest_exit_code": candidate_exit_code,
                            "pytest_summary": (
                                candidate_results.summary_line if candidate_results else None
                            ),
                            "failing_tests": _pytest_failures(candidate_output),
                            "output_excerpt": (candidate_output or "")[-2000:],
                        },
                        step=step,
                    )
                    refresh_model_authoring_summary(
                        session_id=session_id,
                        event_log=event_log,
                        workspace=workspace,
                        validation_result="pending_after_repair",
                        output_artifacts=[],
                    )
                    current_detail = combined
                    current_kind = "pytest"
                    continue
                written, material_failure, file_provenance = promote_repair_candidate_files(
                    session_id=session_id,
                    event_log=event_log,
                    step=step,
                    stage_label=stage_label,
                    workspace=workspace,
                    attempt=repair_attempt,
                    failure_kind="pytest",
                    candidate_paths=candidate_paths,
                    files=repair.repair_candidate_files,
                    provider=repair.repair_provider,
                    model=repair.repair_model,
                )
                if material_failure:
                    return False, material_failure
                finalize_repair_candidate_promotion(
                    session_id=session_id,
                    event_log=event_log,
                    step=step,
                    stage_label=stage_label,
                    written=written,
                    file_provenance=file_provenance,
                )
                return True, current_detail
            if repair.ok:
                return True, current_detail
            latest_failure = repair.technical_detail or repair.message or current_detail
            if current_kind == "pytest":
                current_detail = _combine_named_failure_details(
                    original_label="Original generated pytest failure before repair",
                    original_failure=failure_detail,
                    latest_label="Latest generated pytest repair failure",
                    latest_failure=latest_failure,
                )
            elif failure_kind == "contract_validation":
                current_detail = _combine_named_failure_details(
                    original_label="Original contract validation failure before repair",
                    original_failure=failure_detail,
                    latest_label="Latest contract validation repair failure",
                    latest_failure=latest_failure,
                )
            else:
                current_detail = latest_failure
            if not repair.retryable:
                return False, current_detail
            current_kind = repair.repair_failure_kind or current_kind
        return False, current_detail

    while True:
        for rel_path in contract.all_required_output_paths():
            candidate = workspace / rel_path
            if candidate.is_file():
                candidate.unlink()

        syntax = _preflight_agent_syntax(workspace=workspace, agent_py=agent_py)
        _record_syntax_preflight(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            repair_attempt=repair_attempt,
            syntax=syntax,
        )
        if not syntax.ok:
            detail = syntax.technical_detail or "Generated agent failed Python syntax preflight."
            repaired, detail = await _attempt_repair(
                failure_kind="syntax",
                failure_detail=detail,
            )
            if repaired:
                continue
            detail = _combine_failure_details(
                original_runtime_failure=original_runtime_failure_detail,
                latest_failure=detail,
            )
            _fail(
                event_log=event_log,
                session_id=session_id,
                step=step,
                stage_label=stage_label,
                error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
                message=_author_generated_code_failure_message(detail),
                technical_detail=detail,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.AUTHOR_GENERATED_CODE_FAILED)

        execution_command = [
            sys.executable,
            str(agent_py.relative_to(workspace)),
            *generated_agent_cli_args(
                input_path=ingest.agent_input_path,
                contract_path=contract_rel,
                contract=contract,
            ),
        ]
        execution_command_text = generated_agent_cli_command(
            input_path=ingest.agent_input_path,
            contract_path=contract_rel,
            contract=contract,
        )
        contract_path = workspace / contract_rel
        contract_bytes_before = contract_path.read_bytes() if contract_path.is_file() else b""
        record_tool_action(
            event_log=event_log,
            session_id=session_id,
            step=step,
            tool_name="generated_agent_execution",
            workflow="author",
            phase="author.build",
            controlled_by="generated_code",
            dispatch_mode="orchestrator",
            status="started",
            inputs=[
                str(agent_py.relative_to(workspace)),
                ingest.agent_input_path,
                contract_rel,
            ],
        )
        run_result = sandbox.run(
            session_id=session_id,
            cmd=execution_command,
            cwd_relative=".",
            timeout_seconds=settings.subprocess_timeout_script,
            step=step,
        )
        record_tool_action(
            event_log=event_log,
            session_id=session_id,
            step=step,
            tool_name="generated_agent_execution",
            workflow="author",
            phase="author.build",
            controlled_by="generated_code",
            dispatch_mode="orchestrator",
            status="completed" if run_result.exit_code == 0 else "failed",
            inputs=[
                str(agent_py.relative_to(workspace)),
                ingest.agent_input_path,
                contract_rel,
            ],
            outputs=contract.all_required_output_paths(),
            detail=None if run_result.exit_code == 0 else f"exit_code={run_result.exit_code}",
        )
        contract_bytes_after = contract_path.read_bytes() if contract_path.is_file() else b""
        contract_input_modified = contract_bytes_after != contract_bytes_before
        contract_json_valid_after_execution = True
        if contract_path.is_file():
            try:
                json.loads(contract_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                contract_json_valid_after_execution = False
        missing_outputs = _missing_required_output_paths(workspace=workspace, contract=contract)
        event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "custom_workflow_execution",
                "stage": stage_label,
                "workflow_type": workflow_type,
                "input_path": ingest.agent_input_path,
                "output_path": contract.row_level_output_file,
                "summary_output_files": contract.summary_output_files,
                "exception_output_files": contract.exception_output_files,
                "exit_code": run_result.exit_code,
                "repair_attempt": repair_attempt,
                "command": execution_command_text,
                "missing_required_outputs": missing_outputs,
                "contract_input_modified": contract_input_modified,
                "contract_json_valid_after_execution": contract_json_valid_after_execution,
            },
            step=step,
        )
        if run_result.exit_code != 0 or missing_outputs:
            detail_parts = [
                f"Generated agent exited {run_result.exit_code}.",
                f"command={execution_command_text}",
                f"input_path={ingest.agent_input_path}",
                f"contract_path={contract_rel}",
                f"row_output_path={contract.row_level_output_file}",
            ]
            if missing_outputs:
                detail_parts.append(
                    "missing_required_outputs=" + ", ".join(missing_outputs)
                )
            detail_parts.append(f"contract_input_modified={contract_input_modified}")
            detail_parts.append(
                f"contract_json_valid_after_execution={contract_json_valid_after_execution}"
            )
            detail_parts.extend(
                [
                    f"stdout: {(run_result.stdout or '')[-2000:]}",
                    f"stderr: {(run_result.stderr or '')[-4000:]}",
                    "workspace_artifact_tree:\n" + _workspace_artifact_tree(workspace=workspace),
                ]
            )
            detail = "\n".join(detail_parts)
            original_runtime_failure_detail = detail
            repaired, repair_detail = await _attempt_repair(
                failure_kind="missing_artifacts" if missing_outputs else "execution",
                failure_detail=detail,
            )
            if repaired:
                continue
            detail = _combine_failure_details(
                original_runtime_failure=original_runtime_failure_detail,
                latest_failure=repair_detail,
            )
            _fail(
                event_log=event_log,
                session_id=session_id,
                step=step,
                stage_label=stage_label,
                error_code=ErrorCode.AUTHOR_GENERATED_CODE_FAILED,
                message=_author_generated_code_failure_message(detail),
                technical_detail=detail,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.AUTHOR_GENERATED_CODE_FAILED)
        original_runtime_failure_detail = None
        sanitize_user_facing_workflow_reports(
            workspace=workspace,
            session_id=session_id,
            contract=contract,
        )

        _stage_custom_generated_data(
            workspace=workspace,
            ingest=ingest,
            settings=settings,
            workflow_type=workflow_type,
        )
        test_results, pytest_exit_code, pytest_output = _run_generated_pytest(
            session_id=session_id,
            workspace=workspace,
            settings=settings,
            workspace_manager=workspace_manager,
            step=step,
            stage_label=stage_label,
            event_log=event_log,
        )
        pytest_failed = generated_pytest_gate_failed(test_results, pytest_exit_code)
        if pytest_failed:
            original_pytest_failure_detail = _pytest_failure_detail(
                test_results=test_results,
                exit_code=pytest_exit_code,
                output=pytest_output,
                workspace=workspace,
                contract=contract,
            )
        _rerun_generated_agent_if_output_row_drift(
            workspace=workspace,
            ingest=ingest,
            contract=contract,
            sandbox=sandbox,
            session_id=session_id,
            step=step,
            settings=settings,
        )
        sanitize_user_facing_workflow_reports(
            workspace=workspace,
            session_id=session_id,
            contract=contract,
        )
        golden_ignore_columns: tuple[str, ...] = ()
        golden_config_primary_key: str | None = None
        if bundled_bank_reference_mode and _is_bundled_bank_reference_contract(
            template_hint=template_hint,
            contract=contract,
        ):
            stage_bundled_bank_reference_golden(workspace=workspace)
            contract = apply_bundled_bank_reference_golden_policy(contract)
            golden_ignore_columns = ("rule_matched", "rule_used", "confidence_score")
            golden_config_primary_key = "transaction_id"
        elif _is_expense_exception_review_context(
            template_hint=template_hint,
            schema_profile=schema_profile,
        ):
            if stage_expense_exception_golden(workspace=workspace):
                contract = apply_expense_exception_golden_policy(contract)
                golden_ignore_columns = (
                    "exception_flag",
                    "exception_reason",
                    "severity",
                    "rule_used",
                    "confidence",
                )
                golden_config_primary_key = "expense_id"
        report, all_warnings = validate_against_contract(
            session_id=session_id,
            workspace=workspace,
            contract=contract,
            test_results=test_results,
            golden_ignore_columns=golden_ignore_columns,
            golden_config_primary_key=golden_config_primary_key,
        )
        if bundled_bank_reference_mode and _is_bundled_bank_reference_contract(
            template_hint=template_hint,
            contract=contract,
        ):
            demo_checks = _bank_reference_sample_validation_checks(
                workspace=workspace,
                contract=contract,
            )
            if demo_checks:
                report = report.model_copy(
                    update={
                        "checks": [*report.checks, *demo_checks],
                        "overall_passed": report.overall_passed
                        and all(check.passed is True for check in demo_checks),
                    }
                )
        all_warnings.extend(_semantic_output_warnings(workspace=workspace, contract=contract))
        overwritten = _publish_custom_workflow_validation(
            session_id=session_id,
            workspace=workspace,
            ingest=ingest,
            contract=contract,
            report=report,
            output_abs=output_abs,
            event_log=event_log,
            step=step,
            stage_label=stage_label,
            workflow_type=workflow_type,
        )
        if overwritten:
            message = (
                "System validation report write modified required workflow artifacts: "
                + ", ".join(overwritten)
            )
            _finalize_custom_workflow_failure(
                session_id=session_id,
                workspace=workspace,
                workspace_manager=workspace_manager,
                ingest=ingest,
                contract=contract,
                report=report,
                workflow_type=workflow_type,
                step=step,
                stage_label=stage_label,
                event_log=event_log,
                error_code=ErrorCode.AUTHOR_REQUIRED_ARTIFACT_OVERWRITTEN,
                message=message,
                failed_check=message,
                failed_layer="Validation",
                test_results=test_results,
                warnings=all_warnings,
                technical_detail=message,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.AUTHOR_REQUIRED_ARTIFACT_OVERWRITTEN)

        if pytest_failed:
            message = original_pytest_failure_detail or _pytest_failure_message(
                test_results,
                pytest_exit_code,
            )
            repair_kind = _pytest_failure_repair_kind(
                workspace=workspace,
                contract=contract,
                pytest_output=pytest_output,
            )
            if repair_kind == "contract_validation":
                repair_detail = _contract_validation_failure_detail(
                    workspace=workspace,
                    contract=contract,
                    report=report,
                    execution_command_text=execution_command_text,
                    include_bank_reference_detail=bundled_bank_reference_mode,
                )
                message = (
                    "Original generated pytest failure before repair:\n"
                    f"{message}\n\n"
                    f"{repair_detail}"
                )
            else:
                repair_detail = message
            repaired, message = await _attempt_repair(
                failure_kind=repair_kind,
                failure_detail=repair_detail,
            )
            if repaired:
                continue
            _finalize_custom_workflow_failure(
                session_id=session_id,
                workspace=workspace,
                workspace_manager=workspace_manager,
                ingest=ingest,
                contract=contract,
                report=report,
                workflow_type=workflow_type,
                step=step,
                stage_label=stage_label,
                event_log=event_log,
                error_code=validation_failure_error_code(pytest_failed=True),
                message=message,
                failed_check="Generated pytest",
                failed_layer="generated_pytest",
                test_results=test_results,
                warnings=all_warnings,
                technical_detail=message,
            )
            return (
                SessionStatus.FAILED_OTHER,
                validation_failure_error_code(pytest_failed=True),
            )

        if not report.overall_passed:
            failures = _failed_validation_checks(report)
            primary = _primary_failed_check(report)
            validation_detail = _contract_validation_failure_detail(
                workspace=workspace,
                contract=contract,
                report=report,
                execution_command_text=execution_command_text,
                include_bank_reference_detail=bundled_bank_reference_mode,
            )
            repaired, repair_message = await _attempt_repair(
                failure_kind="contract_validation",
                failure_detail=validation_detail,
            )
            if repaired:
                continue
            technical_detail = repair_message or validation_detail
            contract_error = validation_failure_error_code(report=report)
            _finalize_custom_workflow_failure(
                session_id=session_id,
                workspace=workspace,
                workspace_manager=workspace_manager,
                ingest=ingest,
                contract=contract,
                report=report,
                workflow_type=workflow_type,
                step=step,
                stage_label=stage_label,
                event_log=event_log,
                error_code=contract_error,
                message=primary[0],
                failed_check=primary[0],
                failed_layer=primary[1],
                validation_failures=failures,
                test_results=test_results,
                warnings=all_warnings,
                technical_detail=technical_detail,
            )
            return (SessionStatus.FAILED_OTHER, contract_error)
        break

    sanitize_user_facing_workflow_reports(
        workspace=workspace,
        session_id=session_id,
        contract=contract,
    )

    if report is None:
        _fail(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            error_code=ErrorCode.AUTHOR_VALIDATION_NOT_CONTRACT_DRIVEN,
            message="Contract-driven validation did not run.",
        )
        return (SessionStatus.FAILED_OTHER, ErrorCode.AUTHOR_VALIDATION_NOT_CONTRACT_DRIVEN)

    from agentforge.orchestrator.author_llm_authoring import (
        author_provenance_from_events,
        enforce_author_completion_gate,
    )

    refresh_model_authoring_summary(
        session_id=session_id,
        event_log=event_log,
        workspace=workspace,
        validation_result="pass" if report.overall_passed else "fail",
        output_artifacts=[
            rel for rel in contract.all_required_output_paths() if (workspace / rel).is_file()
        ],
    )

    sync_author_output_contract_provenance(
        session_id=session_id,
        event_log=event_log,
        workspace=workspace,
        contract=contract,
        step=step,
        stage_label=stage_label,
    )

    gate_error = enforce_author_completion_gate(
        session_id=session_id,
        event_log=event_log,
        step=step,
        stage_label=stage_label,
        workspace=workspace,
    )
    if gate_error is not None:
        return (SessionStatus.FAILED_OTHER, gate_error)

    provenance = author_provenance_from_events(event_log.read_all(session_id))
    completion = _build_completion_metadata(
        session_id=session_id,
        workspace=workspace,
        event_log=event_log,
        ingest=ingest,
        contract=contract,
        report=report,
        output_abs=output_abs,
        extra_warnings=all_warnings,
        build_mode=build_mode,
        completion_via=completion_via,
        provenance=provenance,
    )
    workspace_manager.update_manifest(
        session_id,
        status=SessionStatus.COMPLETED.value,
        completion=completion.model_dump(mode="json"),
    )
    sync_manifest_budget_from_events(
        workspace_manager=workspace_manager,
        session_id=session_id,
        events=event_log.read_all(session_id),
    )
    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name="archive_generation",
        workflow="author",
        phase="author.build",
        controlled_by="backend",
        dispatch_mode="orchestrator",
        status="started",
    )
    archive_result = build_archive(session_id=session_id, workspace_manager=workspace_manager)
    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name="archive_generation",
        workflow="author",
        phase="author.build",
        controlled_by="backend",
        dispatch_mode="orchestrator",
        status="completed",
        outputs=[archive_result.relative_path],
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={
            "artifact_type": "archive",
            "path": archive_result.relative_path,
            "hash_sha256": archive_result.hash_sha256,
            "size_bytes": archive_result.size_bytes,
            "file_count": archive_result.file_count,
            "stage": stage_label,
        },
        step=step,
    )
    final_gate = _enforce_final_author_artifacts(
        workspace=workspace,
        contract=contract,
    )
    if final_gate:
        _fail(
            event_log=event_log,
            session_id=session_id,
            step=step,
            stage_label=stage_label,
            error_code=ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE,
            message=f"Completed Author archive is missing required artifacts: {final_gate}",
        )
        workspace_manager.update_manifest(
            session_id,
            status=SessionStatus.FAILED_OTHER.value,
            completion=completion.model_dump(mode="json"),
        )
        return (SessionStatus.FAILED_OTHER, ErrorCode.AUTHOR_MODEL_DID_NOT_CONTRIBUTE)
    return (SessionStatus.COMPLETED, None)


def _preflight_agent_syntax(*, workspace: Path, agent_py: Path) -> AgentSyntaxPreflight:
    try:
        rel = agent_py.relative_to(workspace).as_posix()
    except ValueError:
        rel = agent_py.name
    absolute = str(agent_py)
    try:
        source = agent_py.read_text(encoding="utf-8")
    except OSError as exc:
        detail = (
            "Python syntax preflight failed before executing generated/agent.py.\n"
            f"filename={rel}\n"
            f"absolute_path={absolute}\n"
            f"error=OSError: {exc}"
        )
        return AgentSyntaxPreflight(
            ok=False,
            filename=rel,
            absolute_path=absolute,
            error_type="OSError",
            message=str(exc),
            technical_detail=detail,
        )

    try:
        ast.parse(source, filename=rel)
    except SyntaxError as exc:
        line_number = exc.lineno
        offset = exc.offset
        lines = source.splitlines()
        failing_line = (exc.text or "").rstrip("\n")
        if not failing_line and line_number is not None and 1 <= line_number <= len(lines):
            failing_line = lines[line_number - 1]
        excerpt = _nearby_code_excerpt(lines=lines, line_number=line_number)
        message = exc.msg or str(exc)
        detail_parts = [
            "Python syntax preflight failed before executing generated/agent.py.",
            f"filename={rel}",
            f"absolute_path={absolute}",
            f"error=SyntaxError: {message}",
        ]
        if line_number is not None:
            detail_parts.append(f"line_number={line_number}")
        if offset is not None:
            detail_parts.append(f"offset={offset}")
        if failing_line:
            detail_parts.append(f"failing_line={failing_line}")
        if excerpt:
            detail_parts.append("nearby_code_excerpt:\n" + excerpt)
        return AgentSyntaxPreflight(
            ok=False,
            filename=rel,
            absolute_path=absolute,
            error_type="SyntaxError",
            message=message,
            line_number=line_number,
            offset=offset,
            failing_line=failing_line or None,
            nearby_code_excerpt=excerpt or None,
            technical_detail="\n".join(detail_parts),
        )

    return AgentSyntaxPreflight(ok=True, filename=rel, absolute_path=absolute)


def _nearby_code_excerpt(*, lines: list[str], line_number: int | None) -> str:
    if line_number is None or not lines:
        return ""
    start = max(1, line_number - 2)
    end = min(len(lines), line_number + 2)
    return "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(start, end + 1))


def _combine_failure_details(
    *,
    original_runtime_failure: str | None,
    latest_failure: str,
) -> str:
    return _combine_named_failure_details(
        original_label="Original runtime failure before repair",
        original_failure=original_runtime_failure,
        latest_label="Latest repair failure",
        latest_failure=latest_failure,
    )


def _author_generated_code_failure_message(detail: str, *, max_length: int = 800) -> str:
    """Return a short actionable summary without letting artifact trees dominate."""
    exit_match = re.search(r"Generated agent exited\s+(-?\d+)\.", detail)
    fieldnames_match = _DICTWRITER_FIELDNAMES_VALUEERROR_RE.search(detail)
    if fieldnames_match:
        extra_keys = _parse_dictwriter_extra_fieldnames(fieldnames_match.group("extra"))
        target_match = re.search(
            r"open\(\s*['\"](?P<path>outputs/[^'\"]+\.csv)['\"]",
            detail,
        )
        file_match = re.search(
            r'File "(?P<path>[^"]+generated/agent\.py)", line (?P<line>\d+)',
            detail,
        )
        prefix = (
            f"Generated agent exited {exit_match.group(1)}: "
            if exit_match
            else "Generated agent failed: "
        )
        parts = [
            prefix + "ValueError: dict contains fields not in fieldnames",
        ]
        if target_match:
            parts.append(f"writer target={target_match.group('path')}")
        elif file_match:
            parts.append(
                f"writer in {Path(file_match.group('path')).name}:{file_match.group('line')}"
            )
        if extra_keys:
            preview = ", ".join(extra_keys[:6])
            if len(extra_keys) > 6:
                preview += ", ..."
            parts.append(f"extra keys={preview}")
        parts.append(
            "Expand DictWriter fieldnames or project each row to the declared fieldnames before writerows()."
        )
        return " ".join(parts)[:max_length]

    exception_matches = list(
        re.finditer(
            r"(?P<type>[A-Za-z_][\w.]*(?:Error|Exception)):\s*(?P<message>[^\n]+)",
            detail,
        )
    )
    if exception_matches:
        exc = exception_matches[-1]
        prefix = (
            f"Generated agent exited {exit_match.group(1)}: "
            if exit_match
            else "Generated agent failed: "
        )
        message = f"{prefix}{exc.group('type')}: {exc.group('message')}"
        return message[:max_length]

    missing_match = re.search(r"^missing_required_outputs=(.+)$", detail, re.MULTILINE)
    if missing_match:
        prefix = (
            f"Generated agent exited {exit_match.group(1)}; "
            if exit_match
            else "Generated agent failed; "
        )
        message = (
            prefix
            + "missing required outputs: "
            + missing_match.group(1).strip()
        )
        return message[:max_length]

    syntax_match = re.search(r"SyntaxError:\s*([^\n]+)", detail)
    if syntax_match:
        return ("Generated agent failed syntax preflight: " + syntax_match.group(1))[
            :max_length
        ]

    for line in detail.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("workspace_artifact_tree"):
            return stripped[:max_length]
    return detail[-max_length:]


def _combine_named_failure_details(
    *,
    original_label: str,
    original_failure: str | None,
    latest_label: str,
    latest_failure: str,
) -> str:
    if not original_failure or original_failure.strip() == latest_failure.strip():
        return latest_failure
    return (
        f"{original_label}:\n"
        f"{original_failure}\n\n"
        f"{latest_label}:\n"
        f"{latest_failure}"
    )


def _workspace_artifact_tree(*, workspace: Path, limit: int = 40) -> str:
    roots = ("outputs", "reports", "generated")
    paths: list[str] = []
    for root in roots:
        root_path = workspace / root
        if not root_path.exists():
            continue
        if root_path.is_file():
            paths.append(root)
            continue
        for candidate in sorted(root_path.rglob("*")):
            if not candidate.is_file():
                continue
            paths.append(candidate.relative_to(workspace).as_posix())
            if len(paths) >= limit:
                return "\n".join(paths + ["..."])
    return "\n".join(paths) if paths else "(no files under outputs/, reports/, or generated/)"


def _missing_required_output_paths(*, workspace: Path, contract: AuthorOutputContract) -> list[str]:
    return [
        rel
        for rel in contract.all_required_output_paths()
        if not (workspace / rel).is_file()
    ]


def _record_syntax_preflight(
    *,
    event_log: EventLog,
    session_id: UUID,
    step: int,
    stage_label: str,
    repair_attempt: int,
    syntax: AgentSyntaxPreflight,
) -> None:
    payload: dict[str, object] = {
        "kind": "custom_workflow_syntax_preflight",
        "stage": stage_label,
        "path": syntax.filename,
        "status": "passed" if syntax.ok else "failed",
        "repair_attempt": repair_attempt,
    }
    if not syntax.ok:
        payload.update(
            {
                "error_type": syntax.error_type or "unknown",
                "message": syntax.message or "",
                "absolute_path": syntax.absolute_path,
                "technical_detail": syntax.technical_detail or "",
            }
        )
        if syntax.line_number is not None:
            payload["line_number"] = syntax.line_number
        if syntax.offset is not None:
            payload["offset"] = syntax.offset
        if syntax.failing_line:
            payload["failing_line"] = syntax.failing_line
        if syntax.nearby_code_excerpt:
            payload["nearby_code_excerpt"] = syntax.nearby_code_excerpt
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload=payload,
        step=step,
    )


def _stage_custom_generated_data(
    *,
    workspace: Path,
    ingest: CustomIngestResult,
    settings: Settings,
    workflow_type: str,
) -> None:
    del settings, workflow_type
    data_dir = workspace / "generated" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(workspace / ingest.agent_input_path, data_dir / "sample_input.csv")


def _pytest_failures(
    output: str,
    *,
    failing_tests: list[str] | None = None,
    limit: int = 8,
) -> list[str]:
    failures: list[str] = []
    if failing_tests:
        for name in failing_tests:
            stripped = name.strip()
            if stripped and stripped not in failures:
                failures.append(stripped)
            if len(failures) >= limit:
                return failures
    for line in output.splitlines():
        stripped = line.strip()
        test_name: str | None = None
        if stripped.startswith("FAILED "):
            test_name = stripped[7:].split(" - ", 1)[0].strip()
        elif "::" in stripped and " FAILED" in stripped:
            test_name = stripped.split(" FAILED", 1)[0].strip()
        if not test_name or test_name in failures:
            continue
        failures.append(test_name)
        if len(failures) >= limit:
            break
    return failures


def _pytest_assertion_lines(output: str, *, limit: int = 8) -> list[str]:
    assertions: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        message: str | None = None
        if stripped.startswith("E "):
            message = stripped[2:].strip()
        elif stripped.startswith("AssertionError:"):
            message = stripped
        if not message or message in assertions:
            continue
        if len(message) > 400:
            message = message[:397] + "..."
        assertions.append(message)
        if len(assertions) >= limit:
            break
    return assertions


def _output_csv_snapshot(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
    sample_rows: int = 3,
) -> tuple[str | None, list[dict[str, str]]]:
    output_path = workspace / contract.row_level_output_file
    if not output_path.is_file():
        return None, []
    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = ",".join(reader.fieldnames or [])
        rows: list[dict[str, str]] = []
        for row in reader:
            rows.append(dict(row))
            if len(rows) >= sample_rows:
                break
    return header or None, rows


def _required_report_output_path(contract: AuthorOutputContract) -> str:
    for entry in contract.requested_deliverables:
        path = deliverable_output_path(entry) or deliverable_label(entry)
        if deliverable_required(entry) and path.lower().endswith(".md"):
            return path
    for entry in contract.requested_deliverables:
        path = deliverable_output_path(entry) or deliverable_label(entry)
        if path.lower().endswith(".md"):
            return path
    for entry in contract.summary_output_files:
        path = entry.path if hasattr(entry, "path") else str(entry)
        if path.lower().endswith(".md"):
            return path
    return "reports/validation_report.md"


def _summary_output_excerpt(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
    max_chars: int = 1200,
) -> str | None:
    path = _required_report_output_path(contract)
    output_path = workspace / path
    if not output_path.is_file():
        return None
    body = output_path.read_text(encoding="utf-8")
    if len(body) <= max_chars:
        return body
    return body[: max_chars - 3] + "..."


def _required_column_null_samples(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
    sample_rows: int = 5,
) -> tuple[dict[str, int], list[dict[str, str]]]:
    output_path = workspace / contract.row_level_output_file
    if not output_path.is_file():
        return {}, []
    counts: dict[str, int] = {column: 0 for column in contract.required_output_columns}
    samples: list[dict[str, str]] = []
    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            missing = [
                column
                for column in contract.required_output_columns
                if not str(row.get(column) or "").strip()
            ]
            if not missing:
                continue
            for column in missing:
                counts[column] = counts.get(column, 0) + 1
            if len(samples) < sample_rows:
                sample = dict(row)
                sample["_missing_required_columns"] = ",".join(missing)
                samples.append(sample)
    offenders = {column: count for column, count in counts.items() if count > 0}
    return offenders, samples


def _contract_validation_failure_detail(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
    report: ValidationReport,
    execution_command_text: str,
    include_bank_reference_detail: bool = False,
) -> str:
    lines: list[str] = ["Contract validation failed."]
    failures = _failed_validation_checks(report)
    if failures:
        lines.append("validation_failures:")
        lines.extend(f"- {failure}" for failure in failures)
    if contract.required_output_columns:
        lines.append(
            "required_output_columns=" + ", ".join(contract.required_output_columns)
        )
    header, sample = _output_csv_snapshot(workspace=workspace, contract=contract)
    if header:
        lines.append(f"output_csv_header={header}")
    offenders, failing_rows = _required_column_null_samples(
        workspace=workspace,
        contract=contract,
    )
    if offenders:
        lines.append(f"required_column_null_counts={offenders!r}")
    if failing_rows:
        lines.append("failing_row_samples:")
        lines.extend(f"- {json.dumps(row, sort_keys=True)}" for row in failing_rows)
    elif sample:
        lines.append("output_csv_sample_rows:")
        lines.extend(f"- {json.dumps(row, sort_keys=True)}" for row in sample)
    if include_bank_reference_detail and _is_bundled_bank_reference_contract(
        template_hint=_BUNDLED_BANK_REFERENCE_TEMPLATE,
        contract=contract,
    ):
        observations = _bank_reference_sample_observations(
            workspace=workspace,
            contract=contract,
        )
        lines.append(
            "bundled_bank_demo_expected_category_counts="
            + json.dumps(
                {
                    key: _BUNDLED_BANK_REFERENCE_EXPECTED_CATEGORY_COUNTS[key]
                    for key in _BUNDLED_BANK_REFERENCE_ALLOWED_CATEGORIES
                },
                sort_keys=True,
            )
        )
        lines.append(
            "bundled_bank_demo_actual_category_counts="
            + json.dumps(observations["category_counts"], sort_keys=True)
        )
        if observations["mismatches"]:
            lines.append("bundled_bank_demo_mismatches:")
            lines.extend(
                f"- {json.dumps(item, sort_keys=True)}"
                for item in observations["mismatches"][:12]
            )
    report_excerpt = _summary_output_excerpt(workspace=workspace, contract=contract)
    if report_excerpt:
        lines.append("report_output_excerpt:")
        lines.append(report_excerpt)
    lines.append(f"command={execution_command_text}")
    lines.append("workspace_artifact_tree:\n" + _workspace_artifact_tree(workspace=workspace))
    return "\n".join(lines)


def _pytest_failure_detail(
    *,
    test_results: TestResults | None,
    exit_code: int | None,
    output: str,
    workspace: Path,
    contract: AuthorOutputContract,
) -> str:
    lines: list[str] = [_pytest_failure_message(test_results, exit_code)]
    lines.append("pytest_command=python -m pytest -v generated/tests")
    if test_results is not None:
        lines.append(
            f"pytest_collected={effective_collected_test_count(test_results)}"
        )
        lines.append(f"pytest_min_required={GENERATED_PYTEST_MIN_COLLECTED}")
    if test_results is not None and test_results.summary_line:
        lines.append(f"pytest_summary={test_results.summary_line}")
    failing_tests = _pytest_failures(output)
    if failing_tests:
        lines.append("failing_tests:")
        lines.extend(f"- {name}" for name in failing_tests)
    assertion_lines = _pytest_assertion_lines(output)
    if assertion_lines:
        lines.append("failing_assertions:")
        lines.extend(f"- {message}" for message in assertion_lines)
    header, sample = _output_csv_snapshot(workspace=workspace, contract=contract)
    if header:
        lines.append(f"output_csv_header={header}")
    if sample:
        lines.append("output_csv_sample_rows:")
        lines.extend(f"- {json.dumps(row, sort_keys=True)}" for row in sample)
    report_excerpt = _summary_output_excerpt(workspace=workspace, contract=contract)
    if report_excerpt:
        lines.append("report_output_excerpt:")
        lines.append(report_excerpt)
    offenders, failing_rows = _required_column_null_samples(
        workspace=workspace,
        contract=contract,
    )
    if offenders:
        lines.append(f"required_column_null_counts={offenders!r}")
    if failing_rows:
        lines.append("failing_row_samples:")
        lines.extend(f"- {json.dumps(row, sort_keys=True)}" for row in failing_rows)
    return "\n".join(lines)


def _pytest_failure_requires_agent_repair(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
    pytest_output: str,
) -> bool:
    """True when pytest failed because the agent left required output columns empty."""
    failing_tests = _pytest_failures(pytest_output)
    assertions = _pytest_assertion_lines(pytest_output)
    pytest_signals_required_columns = any(
        "required_column" in name.lower()
        or "required_columns" in name.lower()
        or "non_null" in name.lower()
        or "required_non_null" in name.lower()
        for name in failing_tests
    ) or any(
        "strip()" in message or "is not None" in message
        for message in assertions
    )
    if pytest_signals_required_columns:
        offenders, _ = _required_column_null_samples(workspace=workspace, contract=contract)
        if offenders:
            return True
    if _pytest_signals_classification_agent_failure(assertions):
        return True
    return False


def _pytest_signals_classification_agent_failure(assertions: list[str]) -> bool:
    for message in assertions:
        if "missing from report" in message and "Category '" in message:
            return True
        if "is not in allowed enum" in message:
            return True
        if "category '" in message and "expected false" in message:
            return True
    return False


def _pytest_signals_expense_brittle_test_failure(assertions: list[str]) -> bool:
    """True when pytest failed due to invented enums or exact exception_reason prose."""
    for message in assertions:
        lowered = message.lower()
        if "field 'exception_reason' mismatch" in lowered:
            return True
        if "column 'rule_used'" in lowered and "which is not in allowed set" in lowered:
            return True
        if "unexpected rule_used '" in lowered:
            return True
        if "not in permitted names" in lowered:
            return True
        if "unexpected rule token" in lowered:
            return True
        if "synth_exceptions" in lowered or (
            "no such file" in lowered and "outputs/" in lowered
        ):
            return True
        if (
            "field 'exception_flag' mismatch" in lowered
            and "expected 'no_issue'" in lowered
        ):
            return True
        if "rule name '" in lowered and ";" in message:
            return True
    return False


def _pytest_signals_clear_rule_confidence_test_misinterpretation(
    assertions: list[str],
) -> bool:
    for message in assertions:
        lowered = message.lower()
        if "no rule matched" not in lowered:
            continue
        if "confidence" in lowered and ("< 0.80" in lowered or "< 0.8" in lowered):
            return True
        if "0.4 >=" in lowered or "0.40 >=" in lowered:
            return True
    return False


def _pytest_failure_repair_kind(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
    pytest_output: str,
) -> str:
    """Choose agent repair for output/classification failures; pytest repair for test logic bugs."""
    assertions = _pytest_assertion_lines(pytest_output)
    if _pytest_failure_requires_agent_repair(
        workspace=workspace,
        contract=contract,
        pytest_output=pytest_output,
    ):
        return "contract_validation"
    agent_failure = _pytest_signals_classification_agent_failure(assertions)
    test_only_failure = _pytest_signals_clear_rule_confidence_test_misinterpretation(
        assertions
    )
    expense_brittle_failure = _pytest_signals_expense_brittle_test_failure(assertions)
    if agent_failure:
        return "contract_validation"
    if test_only_failure or expense_brittle_failure:
        return "pytest"
    return "pytest"


def _pytest_candidate_failure_detail(
    *,
    test_results: TestResults | None,
    exit_code: int | None,
    output: str,
    candidate_path: str,
) -> str:
    lines = [_pytest_failure_message(test_results, exit_code)]
    lines.append(f"pytest_command=python -m pytest -v {candidate_path}")
    lines.append(f"candidate_test_path={candidate_path}")
    if test_results is not None and test_results.summary_line:
        lines.append(f"pytest_summary={test_results.summary_line}")
    failing_tests = _pytest_failures(output)
    if failing_tests:
        lines.append("failing_tests:")
        lines.extend(f"- {name}" for name in failing_tests)
    assertion_lines = _pytest_assertion_lines(output)
    if assertion_lines:
        lines.append("failing_assertions:")
        lines.extend(f"- {message}" for message in assertion_lines)
    return "\n".join(lines)


def _run_pytest_target(
    *,
    session_id: UUID,
    workspace: Path,
    settings: Settings,
    workspace_manager: WorkspaceManager,
    step: int,
    stage_label: str,
    event_log: EventLog,
    tests_target: str,
    event_kind: str,
    candidate_attempt: int | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[TestResults | None, int | None, str]:
    tests_path = workspace / tests_target
    if not tests_path.exists():
        return None, None, ""
    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name="generated_pytest",
        workflow="author",
        phase="author.build",
        controlled_by="backend",
        dispatch_mode="orchestrator",
        status="started",
        inputs=[tests_target],
    )
    sandbox = SandboxRunner(settings=settings, workspace_manager=workspace_manager)
    command = [sys.executable, "-m", "pytest", "-v", tests_target]
    result = sandbox.run(
        session_id=session_id,
        cmd=command,
        cwd_relative=".",
        timeout_seconds=settings.subprocess_timeout_pytest,
        step=step,
    )
    parsed = _parse_pytest_minimal(result.stdout, stderr=result.stderr)
    payload: dict[str, object] = {
        "kind": event_kind,
        "stage": stage_label,
        "passed": parsed.passed_count,
        "failed": parsed.failed_count,
        "errors": parsed.error_count,
        "collected": effective_collected_test_count(parsed),
        "exit_code": result.exit_code,
        "summary": parsed.summary_line,
        "command": f"python -m pytest -v {tests_target}",
        "tests_target": tests_target,
        "failing_tests": _pytest_failures(result.stdout or ""),
        "output_excerpt": ((result.stdout or "") + (result.stderr or ""))[-2000:],
    }
    if candidate_attempt is not None:
        payload["attempt"] = candidate_attempt
    if provider:
        payload["provider"] = provider
    if model:
        payload["model"] = model
    pytest_passed = parsed.failed_count == 0 and parsed.error_count == 0
    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name="generated_pytest",
        workflow="author",
        phase="author.build",
        controlled_by="backend",
        dispatch_mode="orchestrator",
        status="completed" if pytest_passed else "failed",
        inputs=[tests_target],
        outputs=[f"reports/{event_kind}_summary"] if event_kind != "pytest_run" else [],
        detail=None if pytest_passed else parsed.summary_line,
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload=payload,
        step=step,
    )
    return parsed, result.exit_code, (result.stdout or "") + (result.stderr or "")


def _count_csv_data_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def _rerun_generated_agent_if_output_row_drift(
    *,
    workspace: Path,
    ingest: CustomIngestResult,
    contract: AuthorOutputContract,
    sandbox: SandboxRunner,
    session_id: UUID,
    step: int,
    settings: Settings,
) -> bool:
    """Re-run the generated agent when pytest left fixture output on production paths."""
    if not production_outputs_need_agent_restore(workspace=workspace, contract=contract):
        return False
    agent_py = workspace / "generated/agent.py"
    if not agent_py.is_file():
        return False
    contract_rel = CONTRACT_REL
    run_result = sandbox.run(
        session_id=session_id,
        cmd=[
            sys.executable,
            str(agent_py.relative_to(workspace)),
            *generated_agent_cli_args(
                input_path=ingest.agent_input_path,
                contract_path=contract_rel,
                contract=contract,
            ),
        ],
        cwd_relative=".",
        timeout_seconds=settings.subprocess_timeout_script,
        step=step,
    )
    return run_result.exit_code == 0


def _run_generated_pytest(
    *,
    session_id: UUID,
    workspace: Path,
    settings: Settings,
    workspace_manager: WorkspaceManager,
    step: int,
    stage_label: str,
    event_log: EventLog,
    tests_target: str = "generated/tests",
    event_kind: str = "pytest_run",
    candidate_attempt: int | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[TestResults | None, int | None, str]:
    return _run_pytest_target(
        session_id=session_id,
        workspace=workspace,
        settings=settings,
        workspace_manager=workspace_manager,
        step=step,
        stage_label=stage_label,
        event_log=event_log,
        tests_target=tests_target,
        event_kind=event_kind,
        candidate_attempt=candidate_attempt,
        provider=provider,
        model=model,
    )


def _write_report(
    *,
    workspace: Path,
    report: ValidationReport,
    system_report_rel: str,
    step: int,
    session_id: UUID,
    stage_label: str,
    event_log: EventLog,
) -> str:
    md_path = workspace / system_report_rel
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_rel = system_validation_report_json_path(system_report_rel)
    json_path = workspace / json_rel
    md_body = render_markdown(report).encode("utf-8")
    json_body = render_json(report).encode("utf-8")
    md_path.write_bytes(md_body)
    json_path.write_bytes(json_body)
    event_log.append(
        session_id=session_id,
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={
            "artifact_type": ArtifactType.SYSTEM_VALIDATION_REPORT.value,
            "path": system_report_rel,
            "hash_sha256": hashlib.sha256(md_body).hexdigest(),
            "size_bytes": len(md_body),
            "overall_passed": report.overall_passed,
            "stage": stage_label,
        },
        step=step,
    )
    return system_report_rel


def _build_completion_metadata(
    *,
    session_id: UUID,
    workspace: Path,
    event_log: EventLog,
    ingest: CustomIngestResult,
    contract: AuthorOutputContract,
    report: ValidationReport,
    output_abs: Path,
    extra_warnings: list[str] | None = None,
    build_mode: str = "llm_authoring",
    completion_via: str = AI_AUTHORED_WORKFLOW_BUILD_VIA,
    provenance: dict[str, Any] | None = None,
) -> CompletionMetadata:
    from agentforge.orchestrator.author_llm_authoring import AI_AUTHORED_WORKFLOW_BUILD_VIA

    events = event_log.read_all(session_id)
    if provenance is None:
        from agentforge.orchestrator.author_llm_authoring import author_provenance_from_events

        provenance = author_provenance_from_events(events)
    (
        artifacts,
        validation_report_path,
        validation_report_hash,
        workflow_report_path,
        workflow_report_hash,
    ) = _artifact_completion_fields_from_events(events)

    skipped_layers = [
        check.layer.value
        for check in report.checks
        if check.skipped
    ]
    validation_summary = [
        ValidationLayerSummary(
            layer=check.layer.value,
            status=("skipped" if check.skipped else ("pass" if check.passed else "fail")),
        )
        for check in report.checks
    ]
    tests_passed = any(
        check.layer == ValidationLayer.GENERATED_PYTEST and check.passed is True
        for check in report.checks
    )
    provenance_artifacts = [
        rel
        for rel in (
            "generated/model_contract_plan.json",
            "generated/model_contract_review.json",
            "generated/model_code_plan.json",
            "generated/model_responses",
            "reports/model_authoring_summary.md",
        )
        if (workspace / rel).exists()
    ]
    output_hash = hashlib.sha256(output_abs.read_bytes()).hexdigest()
    produced_paths = [
        rel
        for rel in contract.all_required_output_paths()
        if (workspace / rel).is_file()
    ]
    contract = contract.finalize_deliverables(produced_paths=produced_paths)
    return CompletionMetadata(
        template_name=None,
        workflow_type=contract.workflow_type,
        build_mode=build_mode,
        completion_via=completion_via or AI_AUTHORED_WORKFLOW_BUILD_VIA,
        recovery_used=False,
        llm_provider=provenance.get("llm_provider"),
        llm_model=provenance.get("llm_model"),
        llm_base_url=provenance.get("llm_base_url"),
        model_call_count=int(provenance.get("model_call_count") or 0),
        model_contributed=bool(provenance.get("model_contributed")),
        model_stages=list(provenance.get("model_stages") or []),
        model_contributed_files=list(provenance.get("model_contributed_files") or []),
        authoring_provenance_artifacts=provenance_artifacts,
        tokens_used=int(provenance.get("tokens_used") or 0),
        input_file=ingest.original_upload_path,
        input_format=ingest.upload_format,
        selected_sheet=ingest.selected_sheet,
        normalized_input_path=ingest.normalized_input_path,
        output_path=contract.row_level_output_file,
        output_hash=output_hash,
        tests_path="generated/tests",
        validation_report_path=validation_report_path,
        validation_report_hash=validation_report_hash,
        workflow_report_path=workflow_report_path,
        workflow_report_hash=workflow_report_hash,
        validation_overall="pass" if report.overall_passed else "fail",
        validation_passed=report.overall_passed,
        tests_passed=tests_passed,
        skipped_layers=skipped_layers,
        warnings=list(contract.warnings) + list(extra_warnings or []),
        artifacts=artifacts,
        validation_summary=validation_summary,
    )


def _artifact_completion_fields_from_events(
    events: list[Any],
) -> tuple[
    list[ArtifactSummary],
    str | None,
    str | None,
    str | None,
    str | None,
]:
    artifacts: list[ArtifactSummary] = []
    validation_report_hash = None
    validation_report_path = None
    workflow_report_path = None
    workflow_report_hash = None
    for evt in events:
        if evt.kind != EventKind.ARTIFACT_GENERATED:
            continue
        payload = evt.payload
        atype = normalize_artifact_type(str(payload.get("artifact_type") or "artifact"))
        path = payload.get("path") or ""
        artifacts.append(
            ArtifactSummary(
                artifact_type=atype,
                path=str(path),
                size_bytes=payload.get("size_bytes"),
                hash_sha256=payload.get("hash_sha256"),
            )
        )
        if atype == ArtifactType.SYSTEM_VALIDATION_REPORT.value:
            validation_report_hash = payload.get("hash_sha256")
            validation_report_path = str(path)
        elif atype == ArtifactType.WORKFLOW_REPORT.value:
            workflow_report_hash = payload.get("hash_sha256")
            workflow_report_path = str(path)
    return (
        artifacts,
        validation_report_path,
        validation_report_hash,
        workflow_report_path,
        workflow_report_hash,
    )


def _pytest_failed(
    test_results: TestResults | None,
    exit_code: int | None,
) -> bool:
    return generated_pytest_gate_failed(test_results, exit_code)


def _only_generated_pytest_failed(report: ValidationReport) -> bool:
    pytest_failed = False
    for check in report.checks:
        if check.skipped:
            continue
        if check.layer == ValidationLayer.GENERATED_PYTEST:
            pytest_failed = check.passed is not True
            continue
        if check.passed is not True:
            return False
    return pytest_failed


def _pytest_failure_message(
    test_results: TestResults | None,
    exit_code: int | None,
) -> str:
    if test_results is not None:
        collected = effective_collected_test_count(test_results)
        if collected < 1 or "no tests collected" in test_results.summary_line.lower():
            return (
                "Generated pytest collected 0 tests; "
                "generated/tests/test_agent.py must define pytest-discoverable "
                "test_* functions with all required imports"
            )
        if collected < GENERATED_PYTEST_MIN_COLLECTED:
            return (
                f"Generated pytest collected only {collected} test(s); "
                f"need at least {GENERATED_PYTEST_MIN_COLLECTED}"
            )
        if test_results.failed_count or test_results.error_count:
            return (
                f"Generated pytest failed: {test_results.failed_count} failed, "
                f"{test_results.passed_count} passed"
            )
        if test_results.passed_count < 1:
            return "Generated pytest did not pass any collected tests"
    if exit_code == 5:
        return "Generated pytest exit code 5: no tests collected"
    if exit_code not in (None, 0):
        return f"Generated pytest exited with code {exit_code}"
    return "Generated pytest failed"


def _enforce_final_author_artifacts(
    *,
    workspace: Path,
    contract: AuthorOutputContract,
) -> list[str]:
    required = [
        "generated/model_contract_plan.json",
        "generated/model_contract_review.json",
        "generated/model_code_plan.json",
        "generated/author_output_contract.json",
        "generated/model_responses",
        "generated/agent.py",
        "generated/tests/test_agent.py",
        "reports/model_authoring_summary.md",
        "manifest.json",
        "events.jsonl",
        "SESSION_README.md",
        "archive.zip",
    ]
    required_paths = set(contract.all_required_output_paths())
    required.append(
        choose_system_validation_report_path(required_paths),
    )
    required.extend(contract.all_required_output_paths())
    missing: list[str] = []
    for rel in required:
        path = workspace / rel
        if not (path.is_file() or path.is_dir()):
            missing.append(rel)
    return missing


def _failed_validation_checks(report: ValidationReport) -> list[str]:
    failures: list[str] = []
    for check in report.checks:
        if check.skipped:
            continue
        if check.passed is True:
            continue
        layer = check.layer.value
        evidence = check.evidence or check.name
        failures.append(f"{check.name} ({layer}): {evidence}")
    return failures


def _human_failed_layer(layer: str) -> str:
    mapping = {
        "row_level": "Row-level invariants",
        "schema": "Output schema",
        "required_columns": "Required columns",
        "business_rules": "Arithmetic checks",
        "golden_output": "Golden comparison",
        "generated_pytest": "Generated pytest",
        "validation": "Validation",
    }
    return mapping.get(layer, layer.replace("_", " ").title())


def _human_validation_failure_message(check: ValidationCheck) -> str:
    evidence = check.evidence or ""
    if check.layer == ValidationLayer.ROW_LEVEL:
        if "not unique" in evidence:
            return (
                "Row-level validation failed because the selected row key was not unique. "
                "The system should use transaction_id or source row number for this file."
            )
        if "row count drift" in evidence:
            return (
                "Row-level validation failed because the output row count does not match "
                "the input file."
            )
    if check.layer == ValidationLayer.GENERATED_PYTEST:
        return "Generated pytest did not pass."
    if check.layer == ValidationLayer.SCHEMA:
        return f"Output schema validation failed: {evidence}"
    if check.layer == ValidationLayer.BUSINESS_RULES:
        return f"Arithmetic or business rule validation failed: {evidence}"
    return f"{check.name}: {evidence}"


def _primary_failed_check(report: ValidationReport) -> tuple[str, str]:
    for check in report.checks:
        if check.skipped or check.passed is True:
            continue
        return (
            _human_validation_failure_message(check),
            _human_failed_layer(check.layer.value),
        )
    return (
        "Custom workflow validation did not pass",
        "Validation",
    )


def _semantic_output_warnings(*, workspace: Path, contract: AuthorOutputContract) -> list[str]:
    """Non-gating warnings derived from produced workflow outputs."""
    warnings: list[str] = []
    output_abs = workspace / contract.row_level_output_file
    if not output_abs.is_file():
        return warnings
    try:
        with output_abs.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return warnings
    if not rows:
        return warnings
    if "category" not in (rows[0] or {}):
        return warnings
    uncategorised_labels = {
        "",
        "uncategorised",
        "uncategorized",
        "unknown",
        "other",
        "unclassified",
        "n/a",
        "na",
    }
    uncategorised = 0
    for row in rows:
        category = (row.get("category") or "").strip().lower()
        if category in uncategorised_labels:
            uncategorised += 1
    ratio = uncategorised / len(rows)
    if ratio >= 0.5:
        warnings.append(
            "High uncategorised ratio in row-level output: "
            f"{uncategorised}/{len(rows)} rows ({ratio:.0%}) lack a confident category."
        )
    elif ratio >= 0.25:
        warnings.append(
            "Elevated uncategorised ratio in row-level output: "
            f"{uncategorised}/{len(rows)} rows ({ratio:.0%}) lack a confident category."
        )
    return warnings


def _publish_custom_workflow_validation(
    *,
    session_id: UUID,
    workspace: Path,
    ingest: CustomIngestResult,
    contract: AuthorOutputContract,
    report: ValidationReport,
    output_abs: Path,
    event_log: EventLog,
    step: int,
    stage_label: str,
    workflow_type: str,
) -> list[str]:
    """Publish validation events and artifact metadata. Returns overwritten workflow paths."""
    del ingest, output_abs
    failures = _failed_validation_checks(report)
    required_paths = set(contract.all_required_output_paths())
    system_report_rel = choose_system_validation_report_path(required_paths)
    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name="deterministic_validation",
        workflow="author",
        phase="author.build",
        controlled_by="backend",
        dispatch_mode="orchestrator",
        status="started",
        inputs=list(contract.all_required_output_paths()),
    )
    record_tool_action(
        event_log=event_log,
        session_id=session_id,
        step=step,
        tool_name="deterministic_validation",
        workflow="author",
        phase="author.build",
        controlled_by="backend",
        dispatch_mode="orchestrator",
        status="completed" if report.overall_passed else "failed",
        inputs=list(contract.all_required_output_paths()),
        outputs=[system_report_rel] if report.overall_passed else [],
        detail=None if report.overall_passed else "; ".join(failures[:3]),
    )
    event_log.append(
        session_id=session_id,
        kind=EventKind.VALIDATION_RUN,
        actor_type=ActorType.SYSTEM,
        payload={
            "overall_passed": report.overall_passed,
            "layer_results": [
                {
                    "layer": check.layer.value,
                    "name": check.name,
                    "passed": check.passed,
                    "skipped": check.skipped,
                    "evidence": check.evidence,
                    "validation_tier": classify_validation_check(check).value,
                }
                for check in report.checks
            ],
            "failed_checks": failures,
            "stage": stage_label,
            "workflow_type": workflow_type,
        },
        step=step,
    )
    before_snapshot = snapshot_required_workflow_artifacts(workspace, contract)
    _write_report(
        workspace=workspace,
        report=report,
        system_report_rel=system_report_rel,
        step=step,
        session_id=session_id,
        stage_label=stage_label,
        event_log=event_log,
    )
    after_snapshot = snapshot_required_workflow_artifacts(workspace, contract)
    overwritten = verify_workflow_artifacts_preserved(before_snapshot, after_snapshot)
    if overwritten:
        return overwritten

    for rel_path in contract.all_required_output_paths():
        artifact_abs = workspace / rel_path
        if not artifact_abs.is_file():
            continue
        body = artifact_abs.read_bytes()
        artifact_type = artifact_type_for_required_path(rel_path)
        event_log.append(
            session_id=session_id,
            kind=EventKind.ARTIFACT_GENERATED,
            actor_type=ActorType.SYSTEM,
            payload={
                "artifact_type": artifact_type,
                "path": rel_path,
                "hash_sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "stage": stage_label,
            },
            step=step,
        )
    return []


def _finalize_custom_workflow_failure(
    *,
    session_id: UUID,
    workspace: Path,
    workspace_manager: WorkspaceManager,
    ingest: CustomIngestResult,
    contract: AuthorOutputContract,
    report: ValidationReport,
    workflow_type: str,
    step: int,
    stage_label: str,
    event_log: EventLog,
    error_code: ErrorCode,
    message: str,
    failed_check: str,
    failed_layer: str,
    validation_failures: list[str] | None = None,
    test_results: TestResults | None = None,
    warnings: list[str] | None = None,
    technical_detail: str | None = None,
) -> None:
    output_abs = workspace / contract.row_level_output_file
    output_hash = (
        hashlib.sha256(output_abs.read_bytes()).hexdigest()
        if output_abs.is_file()
        else None
    )
    (
        artifacts,
        validation_report_path,
        validation_report_hash,
        workflow_report_path,
        workflow_report_hash,
    ) = _artifact_completion_fields_from_events(event_log.read_all(session_id))
    validation_summary = [
        ValidationLayerSummary(
            layer=check.layer.value,
            status=("skipped" if check.skipped else ("pass" if check.passed else "fail")),
        )
        for check in report.checks
    ]
    failure_meta = CompletionFailureMetadata(
        error_code=error_code.value,
        failed_check=failed_check,
        failed_layer=failed_layer,
        validation_failures=validation_failures or _failed_validation_checks(report),
        pytest_summary=test_results.summary_line if test_results else None,
        pytest_passed=test_results.passed_count if test_results else None,
        pytest_failed=test_results.failed_count if test_results else None,
    )
    completion = CompletionMetadata(
        workflow_type=workflow_type,
        build_mode=contract.build_mode,
        completion_via=CUSTOM_WORKFLOW_BUILD_VIA,
        recovery_used=False,
        input_file=ingest.original_upload_path,
        input_format=ingest.upload_format,
        selected_sheet=ingest.selected_sheet,
        normalized_input_path=ingest.normalized_input_path,
        output_path=contract.row_level_output_file if output_abs.is_file() else None,
        output_hash=output_hash,
        tests_path="generated/tests",
        validation_report_path=validation_report_path
        or choose_system_validation_report_path(set(contract.all_required_output_paths())),
        validation_report_hash=validation_report_hash,
        workflow_report_path=workflow_report_path,
        workflow_report_hash=workflow_report_hash,
        validation_overall="fail",
        validation_summary=validation_summary,
        skipped_layers=[check.layer.value for check in report.checks if check.skipped],
        warnings=warnings or list(contract.warnings),
        artifacts=artifacts,
        failure=failure_meta,
    )
    workspace_manager.update_manifest(
        session_id,
        status=SessionStatus.FAILED_OTHER.value,
        completion=completion.model_dump(mode="json"),
    )
    try:
        record_tool_action(
            event_log=event_log,
            session_id=session_id,
            step=step,
            tool_name="archive_generation",
            workflow="author",
            phase="author.build",
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="started",
            detail="failure_mitigation_archive",
        )
        build_archive(session_id=session_id, workspace_manager=workspace_manager)
        record_tool_action(
            event_log=event_log,
            session_id=session_id,
            step=step,
            tool_name="archive_generation",
            workflow="author",
            phase="author.build",
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="completed",
            outputs=["archive.zip"],
            detail="failure_mitigation_archive",
        )
        event_log.append(
            session_id=session_id,
            kind=EventKind.ARTIFACT_GENERATED,
            actor_type=ActorType.SYSTEM,
            payload={
                "artifact_type": "archive",
                "path": "archive.zip",
                "stage": stage_label,
            },
            step=step,
        )
    except Exception as exc:
        _logger.warning("custom workflow failure archive build skipped: %s", exc)
    _fail(
        event_log=event_log,
        session_id=session_id,
        step=step,
        stage_label=stage_label,
        error_code=error_code,
        message=message,
        technical_detail=technical_detail,
        failed_check=failed_check,
        failed_layer=failed_layer,
        validation_failures=validation_failures or _failed_validation_checks(report),
        pytest_summary=test_results.summary_line if test_results else None,
        pytest_passed=test_results.passed_count if test_results else None,
        pytest_failed=test_results.failed_count if test_results else None,
    )


def _fail(
    *,
    event_log: EventLog,
    session_id: UUID,
    step: int,
    stage_label: str,
    error_code: ErrorCode,
    message: str,
    technical_detail: str | None = None,
    failed_check: str | None = None,
    failed_layer: str | None = None,
    validation_failures: list[str] | None = None,
    pytest_summary: str | None = None,
    pytest_passed: int | None = None,
    pytest_failed: int | None = None,
) -> None:
    payload: dict[str, object] = {
        "error_code": error_code.value,
        "message": message,
        "stage": stage_label,
    }
    if technical_detail:
        payload["technical_detail"] = technical_detail
    if failed_check:
        payload["failed_check"] = failed_check
    if failed_layer:
        payload["failed_layer"] = failed_layer
    if validation_failures:
        payload["validation_failures"] = validation_failures
    if pytest_summary:
        payload["pytest_summary"] = pytest_summary
    if pytest_passed is not None:
        payload["pytest_passed"] = pytest_passed
    if pytest_failed is not None:
        payload["pytest_failed"] = pytest_failed
    event_log.append(
        session_id=session_id,
        kind=EventKind.WORKFLOW_FAILED,
        actor_type=ActorType.SYSTEM,
        payload=payload,
        step=step,
    )


__all__ = [
    "CUSTOM_WORKFLOW_BUILD_VIA",
    "CustomIngestResult",
    "UploadedDataFile",
    "execute_custom_workflow_pipeline",
]
