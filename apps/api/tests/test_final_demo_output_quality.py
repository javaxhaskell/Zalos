"""Regression tests for final demo output quality fixes (A–G)."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentforge.orchestrator.author_llm_authoring import (
    _codegen_prompt,
    _contract_stage_guidance,
    _is_expense_exception_schema,
    _test_generation_prompt,
)
from agentforge.orchestrator.repair_proposal import (
    RepairProposal,
    derive_repair_remaining_risks,
    proposal_source_label,
    repair_provenance_from_events,
)
from agentforge.orchestrator.repair_flow import _render_repair_markdown, _sanitize_repair_report_dict
from agentforge.persistence.archive import build_archive
from agentforge.persistence.user_facing import (
    effective_budget_display,
    relativize_workspace_path,
    sanitize_manifest_for_export,
    sanitize_user_facing_text,
    sanitize_user_facing_workflow_reports,
)
from agentforge.schemas import EventKind
from agentforge.schemas.author_output_contract import AuthorOutputContract


class _Event:
    def __init__(self, kind: EventKind, payload: dict, step: int = 0) -> None:
        self.kind = kind
        self.payload = payload
        self.step = step


def test_repair_provenance_from_events_aggregates_tokens_and_contribution() -> None:
    sid = uuid4()
    events = [
        _Event(
            EventKind.MODEL_CALLED,
            {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "base_url": "https://api.deepseek.com",
                "usage": {"input_tokens": 4742, "output_tokens": 88, "total_tokens": 4830},
            },
        ),
        _Event(
            EventKind.DECISION_INPUT,
            {"kind": "repair_proposal", "source": "model"},
        ),
    ]
    provenance = repair_provenance_from_events(events)
    assert provenance["model_call_count"] == 1
    assert provenance["tokens_used"] == 4830
    assert provenance["model_contributed"] is True
    assert provenance["proposal_source"] == "model"


def test_derive_repair_remaining_risks_rejects_stale_comment_hallucination() -> None:
    proposal = RepairProposal(
        root_cause="boundary bug",
        target_file="working/agent.py",
        old_snippet="    if days_overdue <= 31:",
        new_snippet="    if days_overdue <= 30:",
        risk="The comment documenting the bug becomes stale but does not affect correctness.",
        why_this_fix="Fix boundary",
        source="model",
    )
    risks = derive_repair_remaining_risks(
        proposal,
        old_snippet=proposal.old_snippet,
        new_snippet=proposal.new_snippet,
    )
    assert len(risks) == 1
    assert "comment" not in risks[0].lower()
    assert "boundary edge" in risks[0].lower()


def test_render_repair_markdown_surfaces_patch_proposal_source() -> None:
    report = {
        "session_id": str(uuid4()),
        "generated_at": datetime.now(UTC).isoformat(),
        "problem_statement": "problem",
        "primary_problem": {"text": "problem", "source": "built_in_sample_problem_report"},
        "files_inspected": ["working/agent.py"],
        "business_logic_summary": "summary",
        "failure_reproduced": True,
        "before_summary": "2 failed",
        "before_failing_tests": [],
        "root_cause": "off by one",
        "patch_proposal": {
            "source": "model",
            "source_label": proposal_source_label("model"),
            "why_this_fix": "aligns with failing tests",
        },
        "fix_applied": {"file": "working/agent.py", "before": "a", "after": "b"},
        "changed_files": ["working/agent.py"],
        "after_summary": "7 passed",
        "after_passed_count": 7,
        "after_failed_count": 0,
        "post_fix_tests_passed": True,
        "remaining_risks": ["Only the identified boundary edge is changed."],
    }
    md = _render_repair_markdown(report)
    assert "## Patch proposal" in md
    assert "LLM-generated patch proposal" in md
    assert "`model`" in md


def test_test_generation_prompt_forbids_contract_required_artifacts_lookup() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "bank_transaction_categorization",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["transaction_id"],
            "output_columns": ["transaction_id", "category"],
            "required_output_columns": ["category"],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    prompt = json.loads(
        _test_generation_prompt(
            workflow_type="bank_transaction_categorization",
            user_description="Categorise bank transactions.",
            reviewed_contract=contract,
        )
    )
    joined = "\n".join(prompt["test_quality_requirements"]) + prompt["instruction"]
    assert "embedded by the test generator" in joined
    assert "Embed REQUIRED_ARTIFACTS" in joined
    assert "does not persist that key" not in joined.lower()
    assert "required_artifacts key is not persisted" not in joined.lower()


def test_codegen_prompt_forbids_absolute_paths_in_workflow_reports() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Flagged rows",
                    "required_columns": ["expense_id", "exception_flag"],
                    "optional_columns": [],
                }
            ],
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "exception_flag", "confidence"],
            "required_output_columns": ["exception_flag", "confidence"],
            "requested_deliverables": ["reports/validation_report.md"],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="expense_exception_review",
        user_description="Review expense exceptions.",
        reviewed_contract=contract,
    )
    assert "workspace-relative paths only" in prompt
    assert "Never embed absolute filesystem paths" in prompt


def test_exception_review_codegen_guidance_separates_confidence_from_severity() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Flagged rows",
                    "required_columns": ["expense_id", "exception_flag"],
                    "optional_columns": [],
                }
            ],
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "exception_flag", "severity", "confidence"],
            "required_output_columns": ["exception_flag", "severity", "confidence"],
            "requested_deliverables": ["reports/validation_report.md"],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="expense_exception_review",
        user_description="Review expense exceptions.",
        reviewed_contract=contract,
    )
    assert "Exception-review confidence semantics" in prompt
    assert "Do NOT use confidence as a risk score" in prompt
    assert "high severity and high confidence simultaneously" in prompt
    assert "multiple deterministic rules fired" in prompt


def test_sanitize_user_facing_workflow_reports_strips_absolute_paths(tmp_path: Path) -> None:
    sid = uuid4()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id"],
            "required_output_columns": ["expense_id"],
            "requested_deliverables": [
                {
                    "name": "validation_report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                }
            ],
        }
    )
    report_path = workspace / "reports/validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    absolute = f"/Users/demo/Desktop/Zalos/.workspaces/{sid}/uploads/input.csv"
    report_path.write_text(f"Input file: {absolute}\n", encoding="utf-8")
    changed = sanitize_user_facing_workflow_reports(
        workspace=workspace,
        session_id=sid,
        contract=contract,
    )
    assert changed == ["reports/validation_report.md"]
    sanitized = report_path.read_text(encoding="utf-8")
    assert "/Users/demo" not in sanitized
    assert "<workspace-path>" in sanitized or f".workspaces/{sid}" in sanitized


def test_sanitize_user_facing_text_strips_absolute_workspace_paths() -> None:
    sid = uuid4()
    workspace = Path(f"/Users/demo/Desktop/Zalos/.workspaces/{sid}")
    raw = f"command failed in {workspace}/generated/agent.py"
    sanitized = sanitize_user_facing_text(raw, workspace=workspace, session_id=sid)
    assert "/Users/demo" not in sanitized
    assert "./generated/agent.py" in sanitized or f".workspaces/{sid}" in sanitized


def test_sanitize_manifest_for_export_relativizes_workspace_path() -> None:
    sid = str(uuid4())
    manifest = {
        "session_id": sid,
        "workspace_path": f"/Users/demo/Desktop/Zalos/.workspaces/{sid}",
    }
    exported = sanitize_manifest_for_export(manifest)
    assert exported["workspace_path"] == f".workspaces/{sid}"


def test_effective_budget_display_prefers_completion_tokens() -> None:
    display = effective_budget_display(
        {"tokens_used": 34216},
        {"tokens_used": 0, "tool_calls_used": 0, "steps_used": 0, "wall_seconds_used": 0},
    )
    assert display["tokens_used"] == 34216


def test_build_archive_sanitizes_manifest_workspace_path(tool_ctx) -> None:
    workspace = tool_ctx.workspace_manager.get(tool_ctx.session_id)
    sid = str(tool_ctx.session_id)
    manifest_path = workspace / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["workspace_path"] = f"/Users/demo/Desktop/Zalos/.workspaces/{sid}"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (workspace / "outputs" / "output.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = build_archive(
        session_id=tool_ctx.session_id,
        workspace_manager=tool_ctx.workspace_manager,
    )
    with zipfile.ZipFile(result.absolute_path) as zf:
        archived_manifest = json.loads(zf.read("manifest.json"))
    assert archived_manifest["workspace_path"] == relativize_workspace_path(
        session_id=sid,
        absolute_path=manifest["workspace_path"],
    )
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk["workspace_path"].startswith("/Users/demo")


def test_sanitize_repair_report_dict_strips_paths_from_problem_text() -> None:
    sid = uuid4()
    workspace = Path(f"/Users/demo/Desktop/Zalos/.workspaces/{sid}")
    report = {
        "problem_statement": f"Failed in {workspace}/working/agent.py",
        "business_logic_summary": "summary",
        "root_cause": "cause",
    }
    sanitized = _sanitize_repair_report_dict(report, workspace=workspace, session_id=sid)
    assert "/Users/demo" not in str(sanitized["problem_statement"])


def _expense_schema_profile() -> dict[str, object]:
    return {
        "columns": [
            "expense_id",
            "amount",
            "policy_limit",
            "receipt_attached",
            "approval_status",
            "notes",
        ],
        "row_count": 7,
        "upload_format": "csv",
        "agent_input_path": "uploads/input.csv",
        "normalized_input_path": "uploads/input.csv",
    }


def test_expense_schema_guidance_infers_common_rules() -> None:
    assert _is_expense_exception_schema(_expense_schema_profile()) is True
    guidance = _contract_stage_guidance(
        mode="planning",
        schema_profile=_expense_schema_profile(),
    )
    rules = "\n".join(guidance["expense_exception_schema_guidance"]["planning_rules"]).lower()
    assert "amount_over_limit" in rules or "amount > policy_limit" in rules
    assert "missing_receipt" in rules
    assert "approval_not_final" in rules or "not approved" in rules
    assert "suspicious_notes" in rules or "personal" in rules
    assert "review_required" in rules
    assert "exp-001" not in rules
    assert "exp-007" not in rules


def test_non_expense_workflow_does_not_get_expense_rules() -> None:
    bank_profile = {
        "columns": ["txn_id", "amount", "description", "counterparty"],
        "row_count": 10,
    }
    assert _is_expense_exception_schema(bank_profile) is False
    guidance = _contract_stage_guidance(mode="planning", schema_profile=bank_profile)
    assert "expense_exception_schema_guidance" not in guidance


def test_expense_testgen_guidance_for_obvious_exception_cases() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Flagged rows",
                    "required_columns": ["expense_id", "exception_flag"],
                    "optional_columns": [],
                }
            ],
            "exception_rules": [
                {
                    "name": "amount_over_limit",
                    "condition": "amount > policy_limit",
                    "reason": "Amount exceeds policy limit",
                    "severity": "high",
                    "output_column": "exception_flag",
                }
            ],
            "input_columns": ["expense_id", "amount", "policy_limit"],
            "output_columns": ["expense_id", "exception_flag", "review_required"],
            "required_output_columns": ["exception_flag", "review_required"],
            "requested_deliverables": ["outputs/output.csv", "outputs/exceptions.csv"],
        }
    )
    prompt = json.loads(
        _test_generation_prompt(
            workflow_type="expense_exception_review",
            user_description=(
                "Review this expense report, flag policy exceptions, and return all rows "
                "plus a separate exceptions file."
            ),
            reviewed_contract=contract,
            schema_profile=_expense_schema_profile(),
        )
    )
    joined = "\n".join(prompt["test_quality_requirements"])
    assert "obvious exception case" in joined.lower()
    assert "do not hardcode uploaded sample expense_id" in joined.lower()
    assert "stable structured field assertions" in joined.lower()
    assert "Never pass absolute paths" in joined


def test_exception_review_codegen_mentions_approval_and_notes_rules() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Flagged rows",
                    "required_columns": ["expense_id", "exception_flag"],
                    "optional_columns": [],
                }
            ],
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "exception_flag"],
            "required_output_columns": ["exception_flag"],
            "requested_deliverables": ["reports/validation_report.md"],
        }
    )
    prompt = _codegen_prompt(
        workflow_type="expense_exception_review",
        user_description="Review expense exceptions.",
        reviewed_contract=contract,
    )
    assert "approval_status" in prompt
    assert "suspicious" in prompt.lower() or "personal" in prompt.lower()
    assert "Never embed absolute filesystem paths" in prompt


def test_apply_expense_exception_golden_policy_sets_expense_id_primary_key() -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["expense_id"],
            "output_columns": ["expense_id", "review_required"],
            "required_output_columns": ["review_required"],
            "requested_deliverables": ["outputs/output.csv"],
        }
    )
    from agentforge.orchestrator.author_llm_authoring import apply_expense_exception_golden_policy

    updated = apply_expense_exception_golden_policy(contract)
    assert updated.primary_row_key == "expense_id"
    assert updated.golden_comparison_requirement == "required"
    assert updated.golden_output_path == "evals/expected_output.csv"
    assert "review_required" in updated.required_output_columns
    assert updated.allowed_enums.get("review_required") == ["yes", "no"]


def test_apply_expense_exception_golden_policy_normalizes_failed_session_contract() -> None:
    """Regression: session 99a92c59 omitted review_required and over-required exception_reason."""
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "model_authored_finance_workflow",
            "build_mode": "llm_custom",
            "input_file": "uploads/expense_exception_review.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "input_columns": ["expense_id", "amount", "policy_limit"],
            "output_columns": [
                "expense_id",
                "exception_flag",
                "exception_reason",
                "severity",
                "rule_used",
            ],
            "required_output_columns": [
                "expense_id",
                "exception_flag",
                "exception_reason",
            ],
            "output_column_semantics": [
                {
                    "name": "exception_reason",
                    "description": "Reason",
                    "producer_kind": "rule_explanation",
                    "required": True,
                    "nullable": False,
                    "allow_empty_string": False,
                    "row_semantics": "Must always be populated.",
                    "fallback_value_semantics": "Never empty.",
                }
            ],
            "allowed_enums": {
                "exception_flag": ["no_issue", "review_required"],
                "severity": ["low", "medium", "high", "critical", "none"],
            },
            "requested_deliverables": ["outputs/output.csv", "outputs/exceptions.csv"],
        }
    )
    from agentforge.orchestrator.author_llm_authoring import apply_expense_exception_golden_policy

    updated = apply_expense_exception_golden_policy(contract)
    assert updated.workflow_type == "expense_exception_review"
    assert "review_required" in updated.output_columns
    assert updated.required_output_columns == [
        "expense_id",
        "exception_flag",
        "review_required",
    ]
    reason_spec = next(
        spec for spec in updated.output_column_semantics if spec.name == "exception_reason"
    )
    assert reason_spec.nullable is True
    assert reason_spec.allow_empty_string is True
    assert reason_spec.required is False
    review_spec = next(
        spec for spec in updated.output_column_semantics if spec.name == "review_required"
    )
    assert review_spec.required is True
    assert review_spec.nullable is False
    assert updated.allowed_enums.get("exception_flag") == ["yes", "no"]
    flag_spec = next(
        spec for spec in updated.output_column_semantics if spec.name == "exception_flag"
    )
    assert "yes" in flag_spec.row_semantics
    assert "no" in flag_spec.row_semantics


def test_sanitize_user_facing_workflow_reports_without_contract(tmp_path: Path) -> None:
    sid = uuid4()
    workspace = tmp_path / "ws"
    report_path = workspace / "reports/validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    absolute = f"/Users/demo/Desktop/Zalos/.workspaces/{sid}/uploads/input.csv"
    report_path.write_text(f"Input file: {absolute}\n", encoding="utf-8")
    changed = sanitize_user_facing_workflow_reports(
        workspace=workspace,
        session_id=sid,
        contract=None,
    )
    assert changed == ["reports/validation_report.md"]
    assert "/Users/demo" not in report_path.read_text(encoding="utf-8")
