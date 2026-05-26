"""Artifact vs system validation report separation for Author custom builds."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from agentforge.orchestrator.author_custom_build import (
    _build_completion_metadata,
    _finalize_custom_workflow_failure,
    _publish_custom_workflow_validation,
    _semantic_output_warnings,
    _write_report,
)
from agentforge.orchestrator.workflow_artifacts import (
    artifact_type_for_required_path,
    choose_system_validation_report_path,
    normalize_artifact_type,
    snapshot_required_workflow_artifacts,
    system_validation_report_json_path,
    verify_workflow_artifacts_preserved,
    workflow_report_path_from_contract,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    ArtifactType,
    ErrorCode,
    EventKind,
    SessionStatus,
    ValidationCheck,
    ValidationLayer,
    ValidationReport,
    Workflow,
)
from agentforge.schemas.author_output_contract import (
    AuthorOutputContract,
    DeliverableSpec,
)
from agentforge.tools.validation_tools import (
    GenerateValidationReportInput,
    generate_validation_report_handler,
)


def _minimal_contract(**updates: object) -> AuthorOutputContract:
    payload = {
        "workflow_type": "bank_transaction_categorisation",
        "build_mode": "llm_custom",
        "input_file": "uploads/input.csv",
        "input_format": "csv",
        "row_level_output_file": "outputs/output.csv",
        "primary_row_key": "transaction_id",
        "required_output_columns": ["transaction_id", "category", "confidence"],
        "output_column_semantics": [
            {
                "name": "transaction_id",
                "description": "Stable row id",
                "producer_kind": "input",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Copy from input",
                "fallback_value_semantics": "Must exist",
            },
            {
                "name": "category",
                "description": "Assigned category",
                "producer_kind": "derived",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Rule output",
                "fallback_value_semantics": "Uncategorised when no rule matches",
            },
            {
                "name": "confidence",
                "description": "Rule confidence",
                "producer_kind": "derived",
                "required": True,
                "nullable": False,
                "allow_empty_string": False,
                "row_semantics": "Rule confidence score",
                "fallback_value_semantics": "0.0 when uncertain",
            },
        ],
        "requested_deliverables": [
            "outputs/output.csv",
            DeliverableSpec(
                name="validation_report",
                description="Workflow report",
                output_path="reports/validation_report.md",
                required=True,
                source="user_explicit",
            ),
        ],
        "summary_output_files": [
            {
                "path": "reports/validation_report.md",
                "description": "Workflow validation report",
                "required_columns": ["category_counts", "uncertain_rows"],
            }
        ],
    }
    payload.update(updates)
    return AuthorOutputContract.model_validate(payload)


def _sample_report(session_id) -> ValidationReport:
    return ValidationReport(
        session_id=session_id,
        generated_at=datetime.now(UTC),
        overall_passed=True,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.SCHEMA,
                name="Output schema",
                passed=True,
                evidence="ok",
            ),
        ],
    )


def _workspace_bundle(tmp_path: Path) -> tuple[Path, EventLog, object]:
    session_id = uuid4()
    wm = WorkspaceManager(root=tmp_path)
    wm.allocate(session_id, Workflow.AUTHOR)
    event_log = EventLog(wm)
    return wm.get(session_id), event_log, session_id


def test_artifact_type_enum_includes_workflow_and_system_reports() -> None:
    assert ArtifactType.WORKFLOW_ROW_OUTPUT.value == "workflow_row_output"
    assert ArtifactType.WORKFLOW_REPORT.value == "workflow_report"
    assert ArtifactType.SYSTEM_VALIDATION_REPORT.value == "system_validation_report"
    assert ArtifactType.VALIDATION_REPORT.value == "validation_report"


def test_normalize_artifact_type_maps_legacy_validation_report() -> None:
    assert (
        normalize_artifact_type("validation_report")
        == ArtifactType.SYSTEM_VALIDATION_REPORT.value
    )
    assert normalize_artifact_type("workflow_report") == "workflow_report"


def test_choose_system_validation_report_path_avoids_required_paths() -> None:
    assert (
        choose_system_validation_report_path({"reports/validation_report.md"})
        == "reports/system_validation_report.md"
    )
    assert (
        choose_system_validation_report_path(
            {
                "reports/validation_report.md",
                "reports/system_validation_report.md",
            }
        )
        == "reports/agentforge_system_validation_report.md"
    )


def test_artifact_type_for_required_path_maps_csv_and_md() -> None:
    assert (
        artifact_type_for_required_path("outputs/output.csv")
        == ArtifactType.WORKFLOW_ROW_OUTPUT.value
    )
    assert (
        artifact_type_for_required_path("reports/validation_report.md")
        == ArtifactType.WORKFLOW_REPORT.value
    )


def test_system_validation_report_json_path_sidecar() -> None:
    assert (
        system_validation_report_json_path("reports/system_validation_report.md")
        == "reports/system_validation_report.json"
    )


def test_snapshot_and_verify_workflow_artifacts() -> None:
    contract = _minimal_contract()
    workspace = Path("/tmp/unused")
    before = {"outputs/output.csv": "abc"}
    after = {"outputs/output.csv": "abc"}
    assert verify_workflow_artifacts_preserved(before, after) == []
    after_changed = {"outputs/output.csv": "def"}
    assert verify_workflow_artifacts_preserved(before, after_changed) == [
        "outputs/output.csv"
    ]
    del workspace, contract


def test_workflow_report_path_from_contract() -> None:
    contract = _minimal_contract()
    assert workflow_report_path_from_contract(contract) == "reports/validation_report.md"


def test_write_report_writes_system_path_and_emits_system_artifact(
    tmp_path: Path,
) -> None:
    workspace, event_log, session_id = _workspace_bundle(tmp_path)
    report = _sample_report(session_id)
    rel = _write_report(
        workspace=workspace,
        report=report,
        system_report_rel="reports/system_validation_report.md",
        step=1,
        session_id=session_id,
        stage_label="validate",
        event_log=event_log,
    )
    assert rel == "reports/system_validation_report.md"
    assert (workspace / rel).is_file()
    assert (workspace / "reports/system_validation_report.json").is_file()
    assert not (workspace / "reports/validation_report.md").exists()
    events = event_log.read_all(session_id)
    artifact = next(e for e in events if e.kind == EventKind.ARTIFACT_GENERATED)
    assert artifact.payload["artifact_type"] == "system_validation_report"


def test_publish_custom_workflow_validation_preserves_workflow_report(
    tmp_path: Path,
) -> None:
    workspace, event_log, session_id = _workspace_bundle(tmp_path)
    contract = _minimal_contract()
    workflow_report = workspace / "reports/validation_report.md"
    workflow_report.parent.mkdir(parents=True, exist_ok=True)
    workflow_body = b"# Workflow report\nRules and counts from generated agent.\n"
    workflow_report.write_bytes(workflow_body)
    output_csv = workspace / "outputs/output.csv"
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_csv.write_text(
        "transaction_id,category,confidence\n1,Travel,0.95\n",
        encoding="utf-8",
    )
    report = _sample_report(session_id)
    overwritten = _publish_custom_workflow_validation(
        session_id=session_id,
        workspace=workspace,
        ingest=type(
            "Ingest",
            (),
            {
                "original_upload_path": "uploads/input.csv",
                "upload_format": "csv",
                "selected_sheet": None,
                "normalized_input_path": "uploads/input.csv",
            },
        )(),
        contract=contract,
        report=report,
        output_abs=output_csv,
        event_log=event_log,
        step=2,
        stage_label="validate",
        workflow_type=contract.workflow_type,
    )
    assert overwritten == []
    assert workflow_report.read_bytes() == workflow_body
    assert (workspace / "reports/system_validation_report.md").is_file()
    assert "Overall: PASS" in (
        workspace / "reports/system_validation_report.md"
    ).read_text()
    artifacts = [
        e.payload
        for e in event_log.read_all(session_id)
        if e.kind == EventKind.ARTIFACT_GENERATED
    ]
    types_by_path = {item["path"]: item["artifact_type"] for item in artifacts}
    assert types_by_path["reports/system_validation_report.md"] == "system_validation_report"
    assert types_by_path["outputs/output.csv"] == "workflow_row_output"
    assert types_by_path["reports/validation_report.md"] == "workflow_report"
    assert "output_csv" not in types_by_path.values()


def test_publish_detects_required_artifact_overwrite(tmp_path: Path) -> None:
    workspace, _, session_id = _workspace_bundle(tmp_path)
    contract = _minimal_contract()
    workflow_report = workspace / "reports/validation_report.md"
    workflow_report.parent.mkdir(parents=True, exist_ok=True)
    workflow_report.write_text("# Workflow report\n", encoding="utf-8")
    output_csv = workspace / "outputs/output.csv"
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_csv.write_text("transaction_id,category,confidence\n1,A,1.0\n", encoding="utf-8")

    before = snapshot_required_workflow_artifacts(workspace, contract)
    workflow_report.write_text("# mutated by system layer\n", encoding="utf-8")
    after = snapshot_required_workflow_artifacts(workspace, contract)
    assert verify_workflow_artifacts_preserved(before, after) == [
        "reports/validation_report.md"
    ]
    del session_id


def test_build_completion_metadata_tracks_system_and_workflow_reports(
    tmp_path: Path,
) -> None:
    workspace, event_log, session_id = _workspace_bundle(tmp_path)
    contract = _minimal_contract()
    output_csv = workspace / "outputs/output.csv"
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_csv.write_text("transaction_id,category,confidence\n1,A,1.0\n", encoding="utf-8")
    workflow_report = workspace / "reports/validation_report.md"
    workflow_report.parent.mkdir(parents=True, exist_ok=True)
    workflow_report.write_text("# workflow\n", encoding="utf-8")
    report = _sample_report(session_id)
    _publish_custom_workflow_validation(
        session_id=session_id,
        workspace=workspace,
        ingest=type(
            "Ingest",
            (),
            {
                "original_upload_path": "uploads/input.csv",
                "upload_format": "csv",
                "selected_sheet": None,
                "normalized_input_path": "uploads/input.csv",
            },
        )(),
        contract=contract,
        report=report,
        output_abs=output_csv,
        event_log=event_log,
        step=1,
        stage_label="validate",
        workflow_type=contract.workflow_type,
    )
    completion = _build_completion_metadata(
        session_id=session_id,
        workspace=workspace,
        event_log=event_log,
        ingest=type(
            "Ingest",
            (),
            {
                "original_upload_path": "uploads/input.csv",
                "upload_format": "csv",
                "selected_sheet": None,
                "normalized_input_path": "uploads/input.csv",
            },
        )(),
        contract=contract,
        report=report,
        output_abs=output_csv,
    )
    assert completion.validation_report_path == "reports/system_validation_report.md"
    assert completion.workflow_report_path == "reports/validation_report.md"


def test_finalize_failure_records_report_paths_and_preserves_archive_reports(
    tmp_path: Path,
) -> None:
    session_id = uuid4()
    wm = WorkspaceManager(root=tmp_path)
    wm.allocate(session_id, Workflow.AUTHOR)
    workspace = wm.get(session_id)
    event_log = EventLog(wm)
    contract = _minimal_contract()
    output_csv = workspace / "outputs/output.csv"
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_csv.write_text("transaction_id,category,confidence\n1,A,1.0\n", encoding="utf-8")
    workflow_report = workspace / "reports/validation_report.md"
    workflow_report.parent.mkdir(parents=True, exist_ok=True)
    workflow_report.write_text("# workflow\n", encoding="utf-8")
    report = _sample_report(session_id)
    _publish_custom_workflow_validation(
        session_id=session_id,
        workspace=workspace,
        ingest=type(
            "Ingest",
            (),
            {
                "original_upload_path": "uploads/input.csv",
                "upload_format": "csv",
                "selected_sheet": None,
                "normalized_input_path": "uploads/input.csv",
            },
        )(),
        contract=contract,
        report=report,
        output_abs=output_csv,
        event_log=event_log,
        step=1,
        stage_label="validate",
        workflow_type=contract.workflow_type,
    )

    _finalize_custom_workflow_failure(
        session_id=session_id,
        workspace=workspace,
        workspace_manager=wm,
        ingest=type(
            "Ingest",
            (),
            {
                "original_upload_path": "uploads/input.csv",
                "upload_format": "csv",
                "selected_sheet": None,
                "normalized_input_path": "uploads/input.csv",
            },
        )(),
        contract=contract,
        report=report,
        workflow_type=contract.workflow_type,
        step=2,
        stage_label="validate",
        event_log=event_log,
        error_code=ErrorCode.AUTHOR_VALIDATION_FAILED,
        message="failed",
        failed_check="Broken validation",
        failed_layer="row_level",
    )

    manifest = wm.read_manifest(session_id)
    assert manifest.status == SessionStatus.FAILED_OTHER
    assert manifest.completion is not None
    assert manifest.completion.validation_report_path == "reports/system_validation_report.md"
    assert manifest.completion.workflow_report_path == "reports/validation_report.md"

    archive_path = workspace / "archive.zip"
    assert archive_path.is_file()
    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())
    assert "reports/system_validation_report.md" in names
    assert "reports/validation_report.md" in names


def test_semantic_output_warnings_high_uncategorised_ratio(tmp_path: Path) -> None:
    contract = _minimal_contract()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    output_csv = workspace / "outputs/output.csv"
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = ["transaction_id,category,confidence"]
    rows.extend(f"{idx},Uncategorised,0.1" for idx in range(1, 9))
    rows.append("9,Travel,0.95")
    output_csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    warnings = _semantic_output_warnings(workspace=workspace, contract=contract)
    assert any("uncategorised ratio" in warning.lower() for warning in warnings)


@pytest.mark.asyncio
async def test_generate_validation_report_uses_system_path(tool_ctx) -> None:
    report = _sample_report(tool_ctx.session_id)
    out = await generate_validation_report_handler(
        GenerateValidationReportInput(report=report), tool_ctx
    )
    assert out.markdown_path == "reports/system_validation_report.md"
    assert out.json_path == "reports/system_validation_report.json"
    events = tool_ctx.event_log.read_all(tool_ctx.session_id)
    artifact = next(e for e in events if e.kind == EventKind.ARTIFACT_GENERATED)
    assert artifact.payload["artifact_type"] == "system_validation_report"


def test_contract_planning_prompt_mentions_closed_category_lists() -> None:
    from agentforge.orchestrator.author_llm_authoring import _contract_prompt_rules

    rules = "\n".join(_contract_prompt_rules())
    assert "allowed_enums.category" in rules
    assert "classify into" in rules


def test_codegen_report_section_requires_rules_counts_and_review_rows() -> None:
    from agentforge.orchestrator.author_llm_authoring import (
        _report_section_requirements_section,
    )

    contract = _minimal_contract()
    section = _report_section_requirements_section(reviewed_contract=contract)
    assert "uncertain rows" in section.lower()
    assert "human review" in section.lower()


def test_codegen_prompt_mentions_description_and_counterparty() -> None:
    from agentforge.orchestrator.author_llm_authoring import _codegen_prompt

    contract = _minimal_contract()
    prompt = _codegen_prompt(
        workflow_type=contract.workflow_type,
        user_description="Categorise bank transactions",
        reviewed_contract=contract,
    )
    assert "description and counterparty" in prompt.lower()


def test_workflow_artifact_hashes_are_stable(tmp_path: Path) -> None:
    contract = _minimal_contract()
    workspace = tmp_path / "ws"
    path = workspace / "outputs/output.csv"
    path.parent.mkdir(parents=True)
    body = b"transaction_id,category,confidence\n1,A,0.9\n"
    path.write_bytes(body)
    snapshot = snapshot_required_workflow_artifacts(workspace, contract)
    assert snapshot["outputs/output.csv"] == hashlib.sha256(body).hexdigest()
