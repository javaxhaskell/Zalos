"""Regression tests for the four-layer Author validation architecture."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from agentforge.orchestrator.author_contract_validation import validate_against_contract
from agentforge.orchestrator.author_llm_authoring import _codegen_prompt, _test_generation_prompt
from agentforge.schemas import ValidationCheck, ValidationLayer, ValidationReport
from agentforge.schemas.author_output_contract import AuthorOutputContract, DeliverableSpec
from agentforge.schemas.common import ErrorCode
from agentforge.validation import (
    ValidationTier,
    classify_validation_check,
    render_json,
    render_markdown,
    tier_for_check,
    validation_failure_error_code,
)
from agentforge.validation.validation_architecture import TIER_SECTION_COPY
from agentforge.validation.layers import layer_golden_output


def _minimal_contract(**overrides: object) -> AuthorOutputContract:
    payload: dict[str, object] = {
        "workflow_type": "finance_exception_review",
        "build_mode": "llm_custom",
        "input_file": "uploads/input.csv",
        "input_format": "csv",
        "primary_row_key": "txn_id",
        "row_level_output_file": "outputs/output.csv",
        "summary_output_files": [],
        "exception_output_files": [],
        "input_columns": ["txn_id", "amount"],
        "output_columns": ["txn_id", "amount", "category"],
        "required_output_columns": ["txn_id", "category"],
        "optional_output_columns": [],
        "output_column_semantics": [],
        "exception_rules": [],
        "validation_checks": [],
        "requested_deliverables": [
            DeliverableSpec(
                name="validation_report",
                output_path="reports/validation_report.md",
                required=True,
                source="platform_canonical",
            )
        ],
        "preserve_row_count": True,
        "allowed_enums": {"category": ["Income", "Refund"]},
    }
    payload.update(overrides)
    return AuthorOutputContract.model_validate(payload)


def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def test_system_validation_json_includes_validation_tier() -> None:
    report = ValidationReport(
        session_id=uuid4(),
        generated_at=datetime.now(UTC),
        overall_passed=True,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.SCHEMA,
                name="Required deliverables present",
                passed=True,
                evidence="all present",
            ),
            ValidationCheck(
                layer=ValidationLayer.GENERATED_PYTEST,
                name="Generated pytest",
                passed=True,
                evidence="3/3 passed",
            ),
        ],
    )
    payload = json.loads(render_json(report))
    assert payload["checks"][0]["layer"] == ValidationLayer.SCHEMA.value
    assert payload["checks"][0]["validation_tier"] == ValidationTier.UNIVERSAL.value
    assert payload["checks"][1]["validation_tier"] == ValidationTier.GENERATED_PYTEST.value


def test_system_report_groups_universal_and_contract_tiers() -> None:
    report = ValidationReport(
        session_id=uuid4(),
        generated_at=datetime.now(UTC),
        overall_passed=False,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.SCHEMA,
                name="Required deliverables present",
                passed=True,
                evidence="all present",
            ),
            ValidationCheck(
                layer=ValidationLayer.BUSINESS_RULES,
                name="Allowed enum values",
                passed=False,
                evidence="bad enum",
            ),
            ValidationCheck(
                layer=ValidationLayer.GENERATED_PYTEST,
                name="Generated pytest",
                passed=True,
                evidence="3/3 passed",
            ),
            ValidationCheck(
                layer=ValidationLayer.GOLDEN_OUTPUT,
                name="Golden output comparison",
                passed=None,
                skipped=True,
                evidence="no golden staged",
            ),
        ],
    )
    md = render_markdown(report)
    assert "## Universal validation" in md
    assert "## Contract-specific validation" in md
    assert "## Generated pytest" in md
    assert "## Golden-output comparison" in md
    assert "Checks derived from the model-authored AuthorOutputContract" in md
    assert md.index("## Universal validation") < md.index("## Contract-specific validation")
    assert md.index("## Contract-specific validation") < md.index("## Generated pytest")


def test_auxiliary_tier_section_copy_clarifies_provenance() -> None:
    title, description = TIER_SECTION_COPY[ValidationTier.AUXILIARY]
    assert title == "Auxiliary reference checks"
    combined = f"{title} {description}".lower()
    assert "bank template" not in combined
    assert "template run" not in combined
    assert "optional" in description.lower()
    assert "additional validation evidence" in description.lower()
    assert "do not replace the normal author path" in description.lower()
    assert "do not steer code generation" in description.lower()


def test_contract_specific_checks_use_author_output_contract_fields(tmp_path: Path) -> None:
    contract = _minimal_contract(
        exception_output_files=[
            {
                "path": "outputs/exceptions.csv",
                "description": "Flagged rows",
                "required_columns": ["txn_id", "issue_flag"],
                "optional_columns": [],
            }
        ],
        exception_rules=[
            {
                "name": "flag",
                "condition": "issue_flag == 'yes'",
                "output_column": "issue_flag",
                "reason": "review",
                "severity": "medium",
            }
        ],
        output_columns=["txn_id", "amount", "category", "issue_flag"],
        allowed_enums={"category": ["Income", "Refund"], "issue_flag": ["yes", "no"]},
    )
    workspace = tmp_path / "ws"
    _write_csv(
        workspace / "outputs/output.csv",
        ["txn_id", "amount", "category", "issue_flag"],
        [
            {"txn_id": "1", "amount": "10", "category": "Income", "issue_flag": "yes"},
            {"txn_id": "2", "amount": "20", "category": "Refund", "issue_flag": "no"},
        ],
    )
    _write_csv(
        workspace / "outputs/exceptions.csv",
        ["txn_id", "issue_flag"],
        [{"txn_id": "1", "issue_flag": "yes"}],
    )
    _write_csv(
        workspace / "uploads/input.csv",
        ["txn_id", "amount"],
        [{"txn_id": "1", "amount": "10"}, {"txn_id": "2", "amount": "20"}],
    )
    (workspace / "reports").mkdir(parents=True, exist_ok=True)
    (workspace / "reports/validation_report.md").write_text("# report\n", encoding="utf-8")

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=workspace,
        contract=contract,
        test_results=None,
    )
    names = {check.name for check in report.checks}
    assert "Allowed enum values" in names
    assert "Exception list consistency" in names
    assert "Required deliverables present" in names
    assert tier_for_check(
        next(c for c in report.checks if c.name == "Allowed enum values")
    ) == ValidationTier.CONTRACT_SPECIFIC


def test_test_generation_prompt_allows_workflow_specific_behaviour() -> None:
    contract = _minimal_contract()
    prompt = _test_generation_prompt(
        workflow_type="finance_exception_review",
        user_description="Flag policy violations in expenses.",
        reviewed_contract=contract,
    )
    assert "Workflow-specific behavioural tests are allowed and desirable" in prompt
    assert "Contract-specific checks from AuthorOutputContract" in prompt


def test_test_generation_prompt_forbids_overstrict_assertions() -> None:
    contract = _minimal_contract()
    prompt = _test_generation_prompt(
        workflow_type="finance_exception_review",
        user_description="Flag policy violations in expenses.",
        reviewed_contract=contract,
    )
    assert "require every enum label to appear unless sample/contract requires it" in prompt
    assert "require exact report wording unless the contract explicitly requires exact text" in prompt
    assert "assert unsupported confidence or severity assumptions" in prompt


def test_test_generation_prompt_uses_real_contract_artifact_fields() -> None:
    contract = _minimal_contract(
        summary_output_files=[
            {
                "path": "outputs/summary.csv",
                "description": "Summary",
                "required_columns": ["category", "count"],
                "optional_columns": [],
            }
        ]
    )
    prompt = _test_generation_prompt(
        workflow_type="finance_exception_review",
        user_description="Summarise categories.",
        reviewed_contract=contract,
    )
    assert "row_level_output_file" in prompt
    assert "summary_output_files" in prompt
    assert "exception_output_files" in prompt
    assert "requested_deliverables" in prompt
    assert "not only a generic required_artifacts alias" in prompt


def test_golden_output_is_validation_only_in_codegen_prompt() -> None:
    contract = _minimal_contract()
    prompt = _codegen_prompt(
        workflow_type="model_authored_finance_workflow",
        user_description="Categorise transactions.",
        reviewed_contract=contract,
    )
    assert "validation oracles only" in prompt
    assert "must NOT read golden or expected-output files" in prompt
    pre_golden = prompt.split("Golden-output separation:", 1)[0]
    assert "expected_output.csv" not in pre_golden


def test_golden_comparison_passes_and_fails_after_execution(tmp_path: Path) -> None:
    actual = tmp_path / "actual.csv"
    golden = tmp_path / "golden.csv"
    rows = [{"id": "1", "v": "10"}]
    _write_csv(actual, ["id", "v"], rows)
    _write_csv(golden, ["id", "v"], rows)
    passed = layer_golden_output(
        actual_csv=actual,
        golden_csv=golden,
        primary_key="id",
    )
    assert passed.passed is True

    _write_csv(actual, ["id", "v"], [{"id": "1", "v": "99"}])
    failed = layer_golden_output(
        actual_csv=actual,
        golden_csv=golden,
        primary_key="id",
    )
    assert failed.passed is False
    assert classify_validation_check(failed) == ValidationTier.GOLDEN_OUTPUT


def test_failure_classification_distinguishes_layers() -> None:
    pytest_report = ValidationReport(
        session_id=uuid4(),
        generated_at=datetime.now(UTC),
        overall_passed=False,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.GENERATED_PYTEST,
                name="Generated pytest",
                passed=False,
                evidence="1 failed",
            )
        ],
    )
    assert validation_failure_error_code(pytest_failed=True) == ErrorCode.GENERATED_PYTEST_FAILED
    assert (
        validation_failure_error_code(report=pytest_report)
        == ErrorCode.GENERATED_PYTEST_FAILED
    )

    contract_report = ValidationReport(
        session_id=uuid4(),
        generated_at=datetime.now(UTC),
        overall_passed=False,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.BUSINESS_RULES,
                name="Allowed enum values",
                passed=False,
                evidence="bad value",
            )
        ],
    )
    assert (
        validation_failure_error_code(report=contract_report)
        == ErrorCode.CONTRACT_SPECIFIC_VALIDATION_FAILED
    )

    golden_report = ValidationReport(
        session_id=uuid4(),
        generated_at=datetime.now(UTC),
        overall_passed=False,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.GOLDEN_OUTPUT,
                name="Golden output comparison",
                passed=False,
                evidence="drift",
            )
        ],
    )
    assert (
        validation_failure_error_code(report=golden_report)
        == ErrorCode.GOLDEN_OUTPUT_COMPARISON_FAILED
    )

    universal_report = ValidationReport(
        session_id=uuid4(),
        generated_at=datetime.now(UTC),
        overall_passed=False,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.ROW_LEVEL,
                name="Row-level invariants",
                passed=False,
                evidence="row count drift",
            )
        ],
    )
    assert (
        validation_failure_error_code(report=universal_report)
        == ErrorCode.UNIVERSAL_VALIDATION_FAILED
    )
    assert validation_failure_error_code(safety_failed=True) == ErrorCode.SAFETY_VALIDATION_FAILED
    assert validation_failure_error_code(artifact_failed=True) == ErrorCode.ARTIFACT_VALIDATION_FAILED


@pytest.mark.parametrize(
    "fixture_name",
    [
        "bank_generated_pytest_failure_5aa804ba.json",
        "expense_generated_pytest_pathing_failure_fafd3422.json",
        "expense_exception_consistency_ce21e7e5.json",
        "expense_generated_agent_event_log_corruption_3bb42792.json",
    ],
)
def test_regression_fixtures_still_load(fixture_name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    candidates = list(root.glob(f"tests/fixtures/**/{fixture_name}"))
    assert candidates, f"missing fixture {fixture_name}"
    for path in candidates:
        data = path.read_text(encoding="utf-8")
        assert "session_id" in data or "author_output_contract" in data or "failure" in data


def test_author_targeted_suite_passes() -> None:
    api_dir = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_author_output_contract_schema.py",
        "tests/test_author_codegen_reliability.py",
        "tests/test_author_custom_workflow_gate.py",
        "tests/test_author_completion_gate.py",
        "tests/test_generated_pytest_gate.py",
        "tests/test_validation_layers.py",
        "tests/test_config_defaults.py",
        "tests/test_author_artifact_report_separation.py",
        "tests/test_reference_sample_honesty.py",
        "-q",
        "--tb=no",
    ]
    result = subprocess.run(cmd, cwd=api_dir, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_repair_and_final_evidence_tests_pass() -> None:
    api_dir = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_repair_run_endpoint.py",
        "tests/test_final_demo_output_quality.py",
        "-q",
        "--tb=no",
    ]
    result = subprocess.run(cmd, cwd=api_dir, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
