"""Unit tests for the six validation layers (Build Prompt 5c).

Each layer is tested at one happy-path + one failure-path level. The
heavy assertion lives in the integration test that runs the full
bank-categoriser flow to a PASS — these are the regression scaffolding.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from agentforge.orchestrator.author_contract_validation import (
    _is_row_flagged,
    production_outputs_need_agent_restore,
    validate_against_contract,
)
from agentforge.schemas import BusinessRule, ValidationLayer
from agentforge.schemas import TestResults as PytestResultsSchema
from agentforge.schemas.author_output_contract import AuthorOutputContract, DeliverableSpec
from agentforge.validation import (
    golden_diff,
    layer_business_rules,
    layer_generated_pytest,
    layer_golden_output,
    layer_required_columns,
    layer_row_level,
    layer_schema,
    resolve_golden_primary_key,
)


def _write_csv(path: Path, rows: list[dict[str, str]], header: list[str]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def _contract_with_exception_output(*, required_exception_file: bool) -> AuthorOutputContract:
    requested_deliverables: list[object] = [
        "outputs/output.csv",
        DeliverableSpec(
            name="validation_report",
            output_path="reports/validation_report.md",
            required=True,
            source="platform_canonical",
        ),
    ]
    if required_exception_file:
        requested_deliverables.append(
            DeliverableSpec(
                name="exceptions",
                output_path="outputs/exceptions.csv",
                required=True,
                source="user_explicit",
            )
        )
    return AuthorOutputContract.model_validate(
        {
            "workflow_type": "finance_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": ["txn_id", "issue_flag", "issue_reason"],
                    "optional_columns": [],
                }
            ],
            "input_columns": ["txn_id"],
            "output_columns": ["txn_id", "issue_flag"],
            "required_output_columns": [],
            "optional_output_columns": [],
            "output_column_semantics": [],
            "exception_rules": [
                {
                    "name": "review_flag",
                    "condition": "issue_flag == 'yes'",
                    "output_column": "issue_flag",
                    "reason": "Flagged row",
                    "severity": "medium",
                }
            ],
            "validation_checks": [],
            "requested_deliverables": requested_deliverables,
            "preserve_row_count": True,
            "allowed_enums": {"issue_flag": ["yes", "no"]},
        }
    )


# ---------------------------------------------------------------------------
# golden_diff (the engine that backs Layer 5)
# ---------------------------------------------------------------------------


def test_golden_diff_matches_identical_csvs(tmp_path: Path) -> None:
    rows = [{"id": "1", "v": "10"}, {"id": "2", "v": "20"}]
    _write_csv(tmp_path / "a.csv", rows, ["id", "v"])
    _write_csv(tmp_path / "b.csv", rows, ["id", "v"])
    diff = golden_diff(
        actual_csv=tmp_path / "a.csv",
        golden_csv=tmp_path / "b.csv",
        primary_key="id",
    )
    assert diff.matched is True
    assert diff.total_divergences == 0


def test_golden_diff_reports_cell_mismatch(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "a.csv",
        [{"id": "1", "v": "10"}, {"id": "2", "v": "99"}],
        ["id", "v"],
    )
    _write_csv(
        tmp_path / "b.csv",
        [{"id": "1", "v": "10"}, {"id": "2", "v": "20"}],
        ["id", "v"],
    )
    diff = golden_diff(
        actual_csv=tmp_path / "a.csv",
        golden_csv=tmp_path / "b.csv",
        primary_key="id",
    )
    assert diff.matched is False
    assert len(diff.cell_mismatches) == 1
    m = diff.cell_mismatches[0]
    assert m.primary_key == "2"
    assert m.actual == "99"
    assert m.expected == "20"


def test_golden_diff_tolerates_float_drift(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "a.csv", [{"id": "1", "v": "1.0000001"}], ["id", "v"]
    )
    _write_csv(tmp_path / "b.csv", [{"id": "1", "v": "1.0"}], ["id", "v"])
    diff = golden_diff(
        actual_csv=tmp_path / "a.csv",
        golden_csv=tmp_path / "b.csv",
        primary_key="id",
        float_tolerance=1e-5,
    )
    assert diff.matched is True


# ---------------------------------------------------------------------------
# Layer 1 — schema
# ---------------------------------------------------------------------------


def test_layer_schema_passes_when_columns_present(tmp_path: Path) -> None:
    _write_csv(tmp_path / "out.csv", [{"id": "1", "v": "10"}], ["id", "v"])
    check = layer_schema(
        actual_csv=tmp_path / "out.csv", expected_columns=["id", "v"]
    )
    assert check.layer == ValidationLayer.SCHEMA
    assert check.passed is True


def test_layer_schema_fails_when_column_missing(tmp_path: Path) -> None:
    _write_csv(tmp_path / "out.csv", [{"id": "1"}], ["id"])
    check = layer_schema(
        actual_csv=tmp_path / "out.csv", expected_columns=["id", "v"]
    )
    assert check.passed is False
    assert "missing columns" in check.evidence


# ---------------------------------------------------------------------------
# Layer 2 — required columns non-null
# ---------------------------------------------------------------------------


def test_layer_required_columns_passes_when_all_non_null(tmp_path: Path) -> None:
    rows = [{"id": "1", "v": "10"}, {"id": "2", "v": "20"}]
    _write_csv(tmp_path / "out.csv", rows, ["id", "v"])
    check = layer_required_columns(
        actual_csv=tmp_path / "out.csv", required_columns=["id", "v"]
    )
    assert check.passed is True


def test_layer_required_columns_fails_when_null_found(tmp_path: Path) -> None:
    rows = [{"id": "1", "v": ""}, {"id": "2", "v": "20"}]
    _write_csv(tmp_path / "out.csv", rows, ["id", "v"])
    check = layer_required_columns(
        actual_csv=tmp_path / "out.csv", required_columns=["v"]
    )
    assert check.passed is False
    assert "null cells" in check.evidence


# ---------------------------------------------------------------------------
# Layer 3 — business rules / enums
# ---------------------------------------------------------------------------


def test_layer_business_rules_passes_when_no_enums(tmp_path: Path) -> None:
    _write_csv(tmp_path / "out.csv", [{"id": "1", "v": "x"}], ["id", "v"])
    check = layer_business_rules(
        actual_csv=tmp_path / "out.csv",
        rules=[BusinessRule(name="r1", description="something")],
    )
    assert check.passed is True


def test_layer_business_rules_flags_enum_violation(tmp_path: Path) -> None:
    rows = [
        {"id": "1", "category": "Income"},
        {"id": "2", "category": "Unknown"},
    ]
    _write_csv(tmp_path / "out.csv", rows, ["id", "category"])
    check = layer_business_rules(
        actual_csv=tmp_path / "out.csv",
        rules=[],
        allowed_enums={"category": ["Income", "Refund"]},
    )
    assert check.passed is False
    assert "enum violation" in check.evidence


# ---------------------------------------------------------------------------
# Layer 4 — row-level
# ---------------------------------------------------------------------------


def test_layer_row_level_passes_when_row_counts_match(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "in.csv",
        [{"id": "1"}, {"id": "2"}],
        ["id"],
    )
    _write_csv(
        tmp_path / "out.csv",
        [{"id": "1", "v": "a"}, {"id": "2", "v": "b"}],
        ["id", "v"],
    )
    check = layer_row_level(
        actual_csv=tmp_path / "out.csv",
        input_csv=tmp_path / "in.csv",
        primary_key="id",
    )
    assert check.passed is True


def test_layer_row_level_fails_when_row_counts_drift(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "in.csv",
        [{"id": "1"}, {"id": "2"}, {"id": "3"}],
        ["id"],
    )
    _write_csv(
        tmp_path / "out.csv",
        [{"id": "1", "v": "a"}, {"id": "2", "v": "b"}],
        ["id", "v"],
    )
    check = layer_row_level(
        actual_csv=tmp_path / "out.csv",
        input_csv=tmp_path / "in.csv",
        primary_key="id",
    )
    assert check.passed is False
    assert "row count drift" in check.evidence


def test_layer_row_level_skips_without_input_csv(tmp_path: Path) -> None:
    _write_csv(tmp_path / "out.csv", [{"id": "1"}], ["id"])
    check = layer_row_level(
        actual_csv=tmp_path / "out.csv", input_csv=None, primary_key="id"
    )
    assert check.passed is True
    assert "skipped" in check.evidence.lower() or "not provided" in check.evidence.lower()


def test_contract_validation_skips_missing_optional_exception_file(tmp_path: Path) -> None:
    contract = _contract_with_exception_output(required_exception_file=False)
    _write_csv(tmp_path / "uploads" / "input.csv", [{"txn_id": "1"}], ["txn_id"])
    _write_csv(
        tmp_path / "outputs" / "output.csv",
        [{"txn_id": "1", "issue_flag": "no"}],
        ["txn_id", "issue_flag"],
    )
    report_path = tmp_path / "reports" / "validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Validation Report\n", encoding="utf-8")

    report, warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
    ]
    assert exception_check.skipped is True
    assert exception_check.passed is None
    assert "optional exception file not produced: exceptions.csv" == exception_check.evidence
    assert report.overall_passed is True
    assert warnings == []


def test_contract_validation_fails_when_required_exception_file_missing(tmp_path: Path) -> None:
    contract = _contract_with_exception_output(required_exception_file=True)
    _write_csv(tmp_path / "uploads" / "input.csv", [{"txn_id": "1"}], ["txn_id"])
    _write_csv(
        tmp_path / "outputs" / "output.csv",
        [{"txn_id": "1", "issue_flag": "yes"}],
        ["txn_id", "issue_flag"],
    )
    report_path = tmp_path / "reports" / "validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Validation Report\n", encoding="utf-8")

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    required_check = next(check for check in report.checks if check.name == "Required deliverables present")
    assert contract.all_required_output_paths() == [
        "outputs/output.csv",
        "reports/validation_report.md",
        "outputs/exceptions.csv",
    ]
    assert required_check.passed is False
    assert "outputs/exceptions.csv" in required_check.evidence
    assert exception_check.skipped is False
    assert exception_check.passed is False
    assert exception_check.evidence == "exception file missing: exceptions.csv"
    assert report.overall_passed is False


def test_contract_validation_still_checks_present_optional_exception_file(tmp_path: Path) -> None:
    contract = _contract_with_exception_output(required_exception_file=False)
    _write_csv(
        tmp_path / "uploads" / "input.csv",
        [{"txn_id": "1"}, {"txn_id": "2"}],
        ["txn_id"],
    )
    _write_csv(
        tmp_path / "outputs" / "output.csv",
        [
            {"txn_id": "1", "issue_flag": "yes"},
            {"txn_id": "2", "issue_flag": "no"},
        ],
        ["txn_id", "issue_flag"],
    )
    _write_csv(
        tmp_path / "outputs" / "exceptions.csv",
        [],
        ["txn_id", "issue_flag", "issue_reason"],
    )
    report_path = tmp_path / "reports" / "validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Validation Report\n", encoding="utf-8")

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    assert exception_check.skipped is False
    assert exception_check.passed is False
    assert exception_check.evidence == "exception count 0 != flagged rows 1"
    assert report.overall_passed is False


# ---------------------------------------------------------------------------
# Layer 5 — golden output (composes golden_diff)
# ---------------------------------------------------------------------------


def test_layer_golden_output_skips_without_golden(tmp_path: Path) -> None:
    _write_csv(tmp_path / "out.csv", [{"id": "1"}], ["id"])
    check = layer_golden_output(
        actual_csv=tmp_path / "out.csv", golden_csv=None, primary_key="id"
    )
    assert check.passed is None
    assert check.skipped is True
    assert "independent golden" in check.evidence


def test_layer_golden_output_passes_on_match(tmp_path: Path) -> None:
    rows = [{"id": "1", "v": "10"}]
    _write_csv(tmp_path / "out.csv", rows, ["id", "v"])
    _write_csv(tmp_path / "g.csv", rows, ["id", "v"])
    check = layer_golden_output(
        actual_csv=tmp_path / "out.csv",
        golden_csv=tmp_path / "g.csv",
        primary_key="id",
    )
    assert check.passed is True


# ---------------------------------------------------------------------------
# Layer 6 — generated pytest
# ---------------------------------------------------------------------------


def test_layer_pytest_skips_when_no_results() -> None:
    check = layer_generated_pytest(test_results=None)
    assert check.passed is None
    assert check.skipped is True


def test_layer_pytest_passes_when_all_green() -> None:
    from uuid import uuid4

    tr = PytestResultsSchema(
        invocation_id=uuid4(),
        passed_count=3,
        failed_count=0,
        total_count=3,
        collected_count=3,
        summary_line="3 passed in 0.1s",
        per_test=[],
        raw_output_excerpt="",
    )
    check = layer_generated_pytest(test_results=tr)
    assert check.passed is True


def test_layer_pytest_fails_when_no_tests_collected() -> None:
    from uuid import uuid4

    tr = PytestResultsSchema(
        invocation_id=uuid4(),
        passed_count=0,
        failed_count=0,
        total_count=0,
        collected_count=0,
        summary_line="no tests collected",
        per_test=[],
        raw_output_excerpt="",
    )
    check = layer_generated_pytest(test_results=tr)
    assert check.passed is False
    assert "no tests collected" in check.evidence


def test_layer_pytest_fails_when_too_few_tests_collected() -> None:
    from uuid import uuid4

    tr = PytestResultsSchema(
        invocation_id=uuid4(),
        passed_count=1,
        failed_count=0,
        total_count=1,
        collected_count=1,
        summary_line="1 passed in 0.1s",
        per_test=[],
        raw_output_excerpt="",
    )
    check = layer_generated_pytest(test_results=tr)
    assert check.passed is False
    assert "only 1 test(s) collected" in check.evidence


def test_layer_pytest_fails_on_failures() -> None:
    from uuid import uuid4

    tr = PytestResultsSchema(
        invocation_id=uuid4(),
        passed_count=1,
        failed_count=2,
        total_count=3,
        summary_line="2 failed, 1 passed in 0.1s",
        per_test=[],
        raw_output_excerpt="",
    )
    check = layer_generated_pytest(test_results=tr)
    assert check.passed is False
    assert "failed" in check.evidence


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "validation_candidates"


def _expense_input_rows(output_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"expense_id": row["expense_id"], "amount": row["amount"]} for row in output_rows]


def _load_validation_fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _write_validation_report(tmp_path: Path) -> None:
    report_path = tmp_path / "reports" / "validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Validation Report\n", encoding="utf-8")


def test_ce21e7e5_exception_enum_consistency_passes(tmp_path: Path) -> None:
    """Regression for session ce21e7e5: enum exception_flag values must count as flagged."""
    fixture = _load_validation_fixture("expense_exception_consistency_ce21e7e5.json")
    contract = AuthorOutputContract.model_validate(fixture["contract_snippet"])
    header = contract.output_columns
    _write_csv(tmp_path / "uploads" / "input.csv", _expense_input_rows(fixture["output_rows"]), ["expense_id", "amount"])
    _write_csv(tmp_path / "outputs" / "output.csv", fixture["output_rows"], header)
    _write_csv(
        tmp_path / "outputs" / "exceptions.csv",
        fixture["exception_rows"],
        ["expense_id", "exception_flag", "exception_reason", "severity", "rule_used", "confidence"],
    )
    _write_validation_report(tmp_path)

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    assert exception_check.passed is True
    assert exception_check.evidence == fixture["expected_after_fix"]["evidence"]
    assert report.overall_passed is True


def test_contract_validation_boolean_issue_flag_still_works(tmp_path: Path) -> None:
    contract = _contract_with_exception_output(required_exception_file=True)
    _write_csv(tmp_path / "uploads" / "input.csv", [{"txn_id": "1"}, {"txn_id": "2"}], ["txn_id"])
    _write_csv(
        tmp_path / "outputs" / "output.csv",
        [
            {"txn_id": "1", "issue_flag": "yes"},
            {"txn_id": "2", "issue_flag": "no"},
        ],
        ["txn_id", "issue_flag"],
    )
    _write_csv(
        tmp_path / "outputs" / "exceptions.csv",
        [{"txn_id": "1", "issue_flag": "yes", "issue_reason": "Flagged row"}],
        ["txn_id", "issue_flag", "issue_reason"],
    )
    _write_validation_report(tmp_path)

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    assert exception_check.passed is True
    assert "1 exception rows match flagged output rows" == exception_check.evidence


def test_ambiguous_enum_values_do_not_auto_flag(tmp_path: Path) -> None:
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "finance_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "row_level_output_file": "outputs/output.csv",
            "summary_output_files": [],
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Rows flagged for review",
                    "required_columns": ["txn_id", "status_flag"],
                    "optional_columns": [],
                }
            ],
            "input_columns": ["txn_id"],
            "output_columns": ["txn_id", "status_flag"],
            "required_output_columns": [],
            "optional_output_columns": [],
            "output_column_semantics": [],
            "exception_rules": [],
            "validation_checks": [],
            "requested_deliverables": [
                "outputs/output.csv",
                {
                    "name": "exceptions",
                    "output_path": "outputs/exceptions.csv",
                    "required": True,
                    "source": "user_explicit",
                },
                {
                    "name": "validation_report",
                    "output_path": "reports/validation_report.md",
                    "required": True,
                    "source": "platform_canonical",
                },
            ],
            "preserve_row_count": True,
            "allowed_enums": {"status_flag": ["REVIEW", "OK", "PENDING"]},
        }
    )
    assert _is_row_flagged("REVIEW", flag_column="status_flag", contract=contract) is False
    assert _is_row_flagged("PENDING", flag_column="status_flag", contract=contract) is False
    assert _is_row_flagged("OK", flag_column="status_flag", contract=contract) is False


def test_exception_consistency_pk_mismatch_fails(tmp_path: Path) -> None:
    fixture = _load_validation_fixture("expense_exception_consistency_ce21e7e5.json")
    contract = AuthorOutputContract.model_validate(fixture["contract_snippet"])
    header = contract.output_columns
    _write_csv(tmp_path / "uploads" / "input.csv", _expense_input_rows(fixture["output_rows"]), ["expense_id", "amount"])
    _write_csv(tmp_path / "outputs" / "output.csv", fixture["output_rows"], header)
    _write_csv(
        tmp_path / "outputs" / "exceptions.csv",
        [
            {
                "expense_id": "EXP-999",
                "exception_flag": "exception",
                "exception_reason": "Wrong row",
                "severity": "high",
                "rule_used": "amount_over_policy",
                "confidence": "1.0",
            },
            fixture["exception_rows"][1],
        ],
        ["expense_id", "exception_flag", "exception_reason", "severity", "rule_used", "confidence"],
    )
    _write_validation_report(tmp_path)

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    assert exception_check.passed is False
    assert "exception keys mismatch" in exception_check.evidence
    assert "EXP-001" in exception_check.evidence
    assert "EXP-999" in exception_check.evidence


def test_wrong_agent_exception_semantics_still_fail(tmp_path: Path) -> None:
    fixture = _load_validation_fixture("expense_exception_consistency_ce21e7e5.json")
    contract = AuthorOutputContract.model_validate(fixture["contract_snippet"])
    header = contract.output_columns
    output_rows = [
        dict(row, exception_flag="no_issue", exception_reason="no_issue", severity="none", rule_used="none")
        for row in fixture["output_rows"]
    ]
    _write_csv(tmp_path / "uploads" / "input.csv", _expense_input_rows(fixture["output_rows"]), ["expense_id", "amount"])
    _write_csv(tmp_path / "outputs" / "output.csv", output_rows, header)
    _write_csv(
        tmp_path / "outputs" / "exceptions.csv",
        fixture["exception_rows"],
        ["expense_id", "exception_flag", "exception_reason", "severity", "rule_used", "confidence"],
    )
    _write_validation_report(tmp_path)

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    assert exception_check.passed is False
    assert "exception count 2 != flagged rows 0" == exception_check.evidence


def test_validate_against_contract_accepts_mixed_string_summary_metrics(tmp_path: Path) -> None:
    """Regression for da9b613a: plain-string summary_metrics must not crash validation."""
    contract = AuthorOutputContract.model_validate(
        {
            "workflow_type": "expense_exception_review",
            "build_mode": "llm_custom",
            "input_file": "uploads/input.csv",
            "input_format": "csv",
            "primary_row_key": "expense_id",
            "row_level_output_file": "outputs/output.csv",
            "exception_output_files": [
                {
                    "path": "outputs/exceptions.csv",
                    "description": "Flagged rows",
                    "required_columns": ["expense_id", "exception_flag"],
                    "optional_columns": [],
                }
            ],
            "input_columns": ["expense_id", "amount"],
            "output_columns": ["expense_id", "amount", "exception_flag"],
            "required_output_columns": ["expense_id", "exception_flag"],
            "allowed_enums": {"exception_flag": ["yes", "no"]},
            "summary_metrics": [
                "total_rows",
                {
                    "name": "total_exceptions",
                    "metric_type": "count",
                    "source_column": "exception_flag",
                    "filter": "exception_flag == 'yes'",
                    "description": "Count of flagged rows",
                },
            ],
        }
    )
    _write_csv(
        tmp_path / "uploads" / "input.csv",
        [{"expense_id": "E1", "amount": "10"}],
        ["expense_id", "amount"],
    )
    _write_csv(
        tmp_path / "outputs" / "output.csv",
        [{"expense_id": "E1", "amount": "10", "exception_flag": "yes"}],
        ["expense_id", "amount", "exception_flag"],
    )
    _write_csv(
        tmp_path / "outputs" / "exceptions.csv",
        [{"expense_id": "E1", "exception_flag": "yes"}],
        ["expense_id", "exception_flag"],
    )
    _write_validation_report(tmp_path)

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=PytestResultsSchema(
            invocation_id=uuid4(),
            passed_count=4,
            failed_count=1,
            total_count=5,
            summary_line="1 failed, 4 passed in 0.1s",
            per_test=[],
            raw_output_excerpt="",
        ),
    )

    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    assert exception_check.passed is True
    pytest_check = next(check for check in report.checks if check.name == "Generated pytest")
    assert pytest_check.passed is False
    assert report.overall_passed is False


def test_resolve_golden_primary_key_prefers_contract_expense_id(tmp_path: Path) -> None:
    actual = tmp_path / "actual.csv"
    golden = tmp_path / "golden.csv"
    rows = [{"expense_id": "EXP-001", "review_required": "yes"}]
    _write_csv(actual, rows, ["expense_id", "review_required"])
    _write_csv(golden, rows, ["expense_id", "review_required"])
    assert (
        resolve_golden_primary_key(
            actual_csv=actual,
            golden_csv=golden,
            contract_primary_row_key="expense_id",
        )
        == "expense_id"
    )


def test_expense_golden_comparison_uses_expense_id_not_row_id(tmp_path: Path) -> None:
    actual = tmp_path / "outputs" / "output.csv"
    golden = tmp_path / "evals" / "expected_output.csv"
    rows = [
        {"expense_id": "EXP-001", "review_required": "yes"},
        {"expense_id": "EXP-002", "review_required": "yes"},
    ]
    _write_csv(actual, rows, ["expense_id", "review_required"])
    _write_csv(golden, rows, ["expense_id", "review_required"])
    check = layer_golden_output(
        actual_csv=actual,
        golden_csv=golden,
        contract_primary_row_key=None,
        golden_config_primary_key="expense_id",
    )
    assert check.passed is True
    assert "2/2 rows matched golden" in check.evidence


def test_no_silent_row_id_fallback_when_contract_says_expense_id(tmp_path: Path) -> None:
    actual = tmp_path / "actual.csv"
    golden = tmp_path / "golden.csv"
    _write_csv(
        actual,
        [{"expense_id": "EXP-001", "review_required": "yes"}],
        ["expense_id", "review_required"],
    )
    _write_csv(
        golden,
        [{"row_id": "1", "review_required": "yes"}],
        ["row_id", "review_required"],
    )
    check = layer_golden_output(
        actual_csv=actual,
        golden_csv=golden,
        contract_primary_row_key="expense_id",
    )
    assert check.passed is False
    assert "contract primary_row_key 'expense_id'" in check.evidence
    assert "row_id" not in check.evidence.lower() or "missing" in check.evidence.lower()


def test_missing_shared_primary_key_returns_user_facing_error(tmp_path: Path) -> None:
    actual = tmp_path / "actual.csv"
    golden = tmp_path / "golden.csv"
    _write_csv(actual, [{"name": "Alice", "review_required": "yes"}], ["name", "review_required"])
    _write_csv(golden, [{"label": "Alice", "review_required": "yes"}], ["label", "review_required"])
    check = layer_golden_output(
        actual_csv=actual,
        golden_csv=golden,
    )
    assert check.passed is False
    assert "share no row identifier column" in check.evidence
    assert check.hint_if_failed is not None
    assert "expense_id" in check.hint_if_failed


def test_eed97570_multi_rule_rule_used_passes_allowed_enum_validation(tmp_path: Path) -> None:
    fixture = _load_validation_fixture("expense_multi_rule_enum_eed97570.json")
    contract = AuthorOutputContract.model_validate(fixture["contract_snippet"])
    header = contract.output_columns
    _write_csv(
        tmp_path / "uploads" / "expense_exception_review.csv",
        _expense_input_rows(fixture["output_rows"]),
        ["expense_id", "amount"],
    )
    _write_csv(tmp_path / "outputs" / "output.csv", fixture["output_rows"], header)
    _write_csv(
        tmp_path / "outputs" / "exceptions.csv",
        fixture["exception_rows"],
        ["expense_id", "exception_flag", "rule_used"],
    )
    _write_validation_report(tmp_path)

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    enum_check = next(check for check in report.checks if check.name == "Allowed enum values")
    exception_check = next(check for check in report.checks if check.name == "Exception list consistency")
    assert enum_check.passed is True
    assert enum_check.evidence == fixture["expected_after_fix"]["allowed_enum_evidence"]
    assert exception_check.passed is True
    assert exception_check.evidence == fixture["expected_after_fix"]["exception_evidence"]


def test_multi_rule_rule_used_rejects_unknown_token(tmp_path: Path) -> None:
    fixture = _load_validation_fixture("expense_multi_rule_enum_eed97570.json")
    contract = AuthorOutputContract.model_validate(fixture["contract_snippet"])
    header = contract.output_columns
    bad_rows = [*fixture["output_rows"], fixture["invalid_multi_rule_row"]]
    _write_csv(
        tmp_path / "uploads" / "expense_exception_review.csv",
        _expense_input_rows(bad_rows),
        ["expense_id", "amount"],
    )
    _write_csv(tmp_path / "outputs" / "output.csv", bad_rows, header)
    _write_validation_report(tmp_path)

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    enum_check = next(check for check in report.checks if check.name == "Allowed enum values")
    assert enum_check.passed is False
    assert "invented_rule" in enum_check.evidence


def test_single_rule_used_and_non_list_enums_remain_strict(tmp_path: Path) -> None:
    fixture = _load_validation_fixture("expense_multi_rule_enum_eed97570.json")
    contract = AuthorOutputContract.model_validate(fixture["contract_snippet"])
    rows = [
        dict(fixture["output_rows"][0], exception_flag="maybe"),
        fixture["output_rows"][1],
    ]
    _write_csv(
        tmp_path / "uploads" / "expense_exception_review.csv",
        _expense_input_rows(rows),
        ["expense_id", "amount"],
    )
    _write_csv(tmp_path / "outputs" / "output.csv", rows, contract.output_columns)
    _write_validation_report(tmp_path)

    report, _warnings = validate_against_contract(
        session_id=uuid4(),
        workspace=tmp_path,
        contract=contract,
        test_results=None,
    )

    enum_check = next(check for check in report.checks if check.name == "Allowed enum values")
    assert enum_check.passed is False
    assert "exception_flag='maybe'" in enum_check.evidence


def test_production_outputs_need_restore_when_exceptions_csv_corrupted(tmp_path: Path) -> None:
    fixture = _load_validation_fixture("expense_multi_rule_enum_eed97570.json")
    contract = AuthorOutputContract.model_validate(fixture["contract_snippet"])
    header = contract.output_columns
    _write_csv(
        tmp_path / "uploads" / "expense_exception_review.csv",
        _expense_input_rows(fixture["output_rows"]),
        ["expense_id", "amount"],
    )
    _write_csv(tmp_path / "outputs" / "output.csv", fixture["output_rows"], header)
    _write_csv(
        tmp_path / "outputs" / "exceptions.csv",
        [{"expense_id": "TEST-001", "exception_flag": "exception", "rule_used": "amount_over_limit"}],
        ["expense_id", "exception_flag", "rule_used"],
    )

    assert production_outputs_need_agent_restore(workspace=tmp_path, contract=contract) is True


def test_layer_business_rules_accepts_semicolon_rule_used(tmp_path: Path) -> None:
    csv_path = tmp_path / "output.csv"
    _write_csv(
        csv_path,
        [
            {
                "expense_id": "EXP-007",
                "rule_used": "amount_over_limit; missing_receipt; approval_not_final; suspicious_notes",
            }
        ],
        ["expense_id", "rule_used"],
    )
    check = layer_business_rules(
        actual_csv=csv_path,
        rules=[],
        allowed_enums={
            "rule_used": [
                "amount_over_limit",
                "missing_receipt",
                "approval_not_final",
                "suspicious_notes",
                "none",
            ]
        },
    )
    assert check.passed is True
