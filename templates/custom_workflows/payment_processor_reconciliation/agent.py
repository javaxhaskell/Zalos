"""Payment processor reconciliation agent.

Reads a normalised settlement CSV and an output contract JSON, then writes
every deliverable required by the contract.

Usage:
    python agent.py <input_csv> <contract_json>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from reconcile import exception_rows, reconcile_row, summarize_by_group

OUTPUT_COLUMNS = (
    "calculated_net_amount",
    "expected_net_amount",
    "net_amount_difference",
    "reconciliation_status",
    "issue_flag",
    "rule_used",
    "confidence",
)


def _load_contract(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare payment processor settlements for finance review."
    )
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("contract_json", type=Path)
    args = parser.parse_args(argv)

    if not args.input_csv.is_file():
        print(f"error: input file not found: {args.input_csv}", file=sys.stderr)
        return 2
    if not args.contract_json.is_file():
        print(f"error: contract file not found: {args.contract_json}", file=sys.stderr)
        return 2

    contract = _load_contract(args.contract_json)
    row_output = Path(contract.get("row_level_output_file", "outputs/output.csv"))

    with args.input_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]

    for row in rows:
        calc, expected, diff, status, issue, rule, confidence = reconcile_row(row)
        row["calculated_net_amount"] = calc
        row["expected_net_amount"] = expected
        row["net_amount_difference"] = diff
        row["reconciliation_status"] = status
        row["issue_flag"] = issue
        row["rule_used"] = rule
        row["confidence"] = f"{confidence:.2f}"

    if rows:
        input_columns = [c for c in rows[0] if c not in OUTPUT_COLUMNS]
        fieldnames = input_columns + list(OUTPUT_COLUMNS)
    else:
        fieldnames = list(OUTPUT_COLUMNS)

    _write_csv(row_output, fieldnames, rows)

    for spec in contract.get("aggregation_specs") or []:
        group_key = spec["group_key"]
        summary_path = Path(spec["output_path"])
        summary_rows = summarize_by_group(rows, group_key)
        summary_fields = [
            group_key,
            "transaction_count",
            "gross_total",
            "fee_total",
            "net_total",
            "calculated_net_total",
            "issue_count",
        ]
        _write_csv(summary_path, summary_fields, summary_rows)

    for exception_path in contract.get("exception_output_files") or []:
        flagged = exception_rows(rows)
        exc_fields = fieldnames if flagged else fieldnames
        _write_csv(Path(exception_path), exc_fields, flagged)

    print(
        f"Reconciled {len(rows)} settlement rows. "
        f"Wrote {row_output} and {len(contract.get('aggregation_specs') or [])} summary file(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
