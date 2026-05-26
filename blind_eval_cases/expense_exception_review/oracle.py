"""Independent oracle for the expense exception blind Author case."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED_OUTPUT_COLUMNS = {
    "expense_id",
    "exception_flag",
    "exception_reason",
    "severity",
    "rule_used",
    "confidence",
}
SUSPICIOUS_TERMS = ("personal", "duplicate", "missing receipt", "cash advance")
MISSING_RECEIPT_THRESHOLD = Decimal("100.00")


def run_oracle(*, workspace: Path, input_path: Path) -> tuple[bool, str]:
    output_path = workspace / "outputs" / "output.csv"
    exceptions_path = workspace / "outputs" / "exceptions.csv"
    required_artifacts = [
        output_path,
        exceptions_path,
        workspace / "generated" / "agent.py",
        workspace / "generated" / "tests" / "test_agent.py",
        workspace / "generated" / "model_contract_plan.json",
        workspace / "generated" / "model_contract_review.json",
        workspace / "generated" / "model_code_plan.json",
        workspace / "reports" / "validation_report.md",
        workspace / "reports" / "model_authoring_summary.md",
        workspace / "manifest.json",
        workspace / "events.jsonl",
        workspace / "SESSION_README.md",
        workspace / "archive.zip",
    ]
    missing = [str(path.relative_to(workspace)) for path in required_artifacts if not path.is_file()]
    if missing:
        return False, f"missing required artifacts: {missing}"

    input_rows = _read_csv(input_path)
    output_rows = _read_csv(output_path)
    exception_rows = _read_csv(exceptions_path)
    if len(output_rows) != len(input_rows):
        return False, f"row count drift: input={len(input_rows)} output={len(output_rows)}"
    if not output_rows:
        return False, "output.csv has no rows"
    missing_columns = REQUIRED_OUTPUT_COLUMNS.difference(output_rows[0].keys())
    if missing_columns:
        return False, f"output.csv missing required columns: {sorted(missing_columns)}"

    expected_flagged = {
        row["expense_id"]
        for row in input_rows
        if _expected_flag(row)
    }
    actual_flagged = {
        row.get("expense_id", "")
        for row in output_rows
        if _truthy(row.get("exception_flag"))
    }
    if actual_flagged != expected_flagged:
        return (
            False,
            f"flag mismatch: expected={sorted(expected_flagged)} actual={sorted(actual_flagged)}",
        )

    exceptions_ids = {row.get("expense_id", "") for row in exception_rows}
    if exceptions_ids != actual_flagged:
        return (
            False,
            f"exceptions.csv mismatch: expected={sorted(actual_flagged)} actual={sorted(exceptions_ids)}",
        )

    for row in output_rows:
        if row.get("expense_id") not in expected_flagged:
            continue
        for column in ("exception_reason", "severity", "rule_used", "confidence"):
            if not str(row.get(column) or "").strip():
                return False, f"flagged row {row.get('expense_id')} missing {column}"
    return True, "expense exception oracle passed"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _decimal(value: str | None) -> Decimal:
    try:
        return Decimal(str(value or "0").strip())
    except InvalidOperation:
        return Decimal("0")


def _expected_flag(row: dict[str, str]) -> bool:
    amount = _decimal(row.get("amount"))
    policy_limit = _decimal(row.get("policy_limit"))
    if amount > policy_limit:
        return True
    receipt = str(row.get("receipt_attached") or "").strip().lower()
    if receipt in {"false", "no", "0", "n"} and amount > MISSING_RECEIPT_THRESHOLD:
        return True
    if str(row.get("approval_status") or "").strip().lower() != "approved":
        return True
    notes = str(row.get("notes") or "").lower()
    return any(term in notes for term in SUSPICIOUS_TERMS)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "y", "1", "flagged"}
