"""Tests for the payment processor reconciliation custom workflow."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE_ROOT = HERE.parent
DATA_DIR = TEMPLATE_ROOT / "data"
AGENT = TEMPLATE_ROOT / "agent.py"

INPUT_COLUMNS = (
    "processor",
    "settlement_date",
    "merchant_id",
    "gross_amount",
    "fee",
    "net_amount",
)
OUTPUT_COLUMNS = (
    "calculated_net_amount",
    "expected_net_amount",
    "net_amount_difference",
    "reconciliation_status",
    "issue_flag",
    "rule_used",
    "confidence",
)


def _contract_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workflow_type": "payment_processor_reconciliation",
        "row_level_output_file": "out.csv",
        "aggregation_specs": [],
        "exception_output_files": [],
    }
    payload.update(overrides)
    return payload


def run_agent(
    input_csv: Path,
    output_csv: Path,
    *,
    contract: dict[str, object] | None = None,
) -> None:
    contract_path = output_csv.parent / "contract.json"
    contract_body = dict(contract or _contract_payload())
    contract_body["row_level_output_file"] = str(output_csv.name)
    contract_path.write_text(json.dumps(contract_body), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(AGENT), str(input_csv), str(contract_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(output_csv.parent),
    )
    assert result.returncode == 0, result.stderr


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_output_schema_includes_required_columns(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    rows = read_csv(out)
    assert rows
    keys = set(rows[0].keys())
    for col in INPUT_COLUMNS:
        assert col in keys
    for col in OUTPUT_COLUMNS:
        assert col in keys


def test_row_count_preserved(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    assert len(read_csv(out)) == len(read_csv(DATA_DIR / "sample_input.csv"))


def test_calculated_fields_populated(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    for row in read_csv(out):
        assert row["calculated_net_amount"]
        assert row["expected_net_amount"]
        assert row["reconciliation_status"]
        assert row["issue_flag"] in {"yes", "no"}
        assert row["rule_used"]
        assert row["confidence"]


def test_matched_row_has_zero_difference(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    matched = [r for r in read_csv(out) if r["reconciliation_status"] == "matched"]
    assert matched
    for row in matched:
        assert Decimal(row["net_amount_difference"]) == Decimal("0.00")


def test_issue_rows_flagged(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    investigate = [r for r in read_csv(out) if r["reconciliation_status"] == "investigate"]
    assert investigate
    for row in investigate:
        assert row["issue_flag"] == "yes"


def test_processor_export_schema_with_fee_amount(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    source = DATA_DIR / "processor_export_sample.csv"
    run_agent(source, out)
    rows = read_csv(out)
    assert len(rows) == len(read_csv(source))
    for row in rows:
        assert row["calculated_net_amount"]
        assert row["expected_net_amount"]
        assert row["net_amount_difference"]
        assert row["reconciliation_status"] in {
            "matched",
            "investigate",
            "needs_review",
        }
        gross = Decimal(row["gross_amount"])
        fee = Decimal(row["fee_amount"])
        calc = Decimal(row["calculated_net_amount"])
        reported = Decimal(row["net_amount"])
        diff = Decimal(row["net_amount_difference"])
        assert abs((gross - fee) - calc) <= Decimal("0.01")
        assert abs((calc - reported) - diff) <= Decimal("0.01")


def test_issue_rows_have_issue_flag(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    source = DATA_DIR / "processor_export_sample.csv"
    run_agent(source, out)
    flagged = [r for r in read_csv(out) if r["issue_flag"] == "yes"]
    assert flagged


def test_summary_by_settlement_batch_when_contract_requires(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    summary = tmp_path / "summary_by_settlement_batch.csv"
    source = DATA_DIR / "processor_export_sample.csv"
    run_agent(
        source,
        out,
        contract=_contract_payload(
            aggregation_specs=[
                {
                    "group_key": "settlement_batch",
                    "output_path": str(summary.name),
                    "metrics": ["transaction_count", "gross_total"],
                }
            ]
        ),
    )
    assert summary.is_file()
    summary_rows = read_csv(summary)
    assert summary_rows
    assert "transaction_count" in summary_rows[0]
