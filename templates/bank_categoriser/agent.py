"""Bank transaction categoriser.

Reads a CSV of bank transactions and writes the same rows with four new
columns:

  * ``category``      — one of six finance categories.
  * ``rule_matched``  — trace string for the rule that fired.
  * ``rule_used``     — same as ``rule_matched`` (the workflow contract
    in :file:`WORKFLOWS.md` requested both names; we emit both rather
    than drop one silently).
  * ``confidence``    — float in ``[0, 1]`` reflecting which rule fired
    (refund=1.0, description patterns=0.95, vendor map=0.70,
    Uncategorised=0.0).

Usage:
    python agent.py <input_csv> <output_csv>

Categories: Income, Office Expense, Travel, Subscriptions, Refund, Uncategorised.
Refund-first override: any row with amount < 0 is tagged Refund regardless
of description.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from rules import categorise_row

OUTPUT_COLUMNS = ("category", "rule_matched", "rule_used", "confidence")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Categorise bank transactions.")
    parser.add_argument("input_csv", type=Path, help="Input CSV path.")
    parser.add_argument("output_csv", type=Path, help="Output CSV path.")
    args = parser.parse_args(argv)

    if not args.input_csv.exists():
        print(f"error: input file not found: {args.input_csv}", file=sys.stderr)
        return 2

    with args.input_csv.open(newline="") as fin:
        reader = csv.DictReader(fin)
        rows = [dict(r) for r in reader]

    if not rows:
        print("warning: input has no data rows", file=sys.stderr)

    for row in rows:
        category, rule_matched, confidence = categorise_row(row)
        row["category"] = category
        row["rule_matched"] = rule_matched
        row["rule_used"] = rule_matched
        row["confidence"] = f"{confidence:.2f}"

    if rows:
        input_columns = [c for c in rows[0] if c not in OUTPUT_COLUMNS]
        fieldnames = input_columns + list(OUTPUT_COLUMNS)
    else:
        fieldnames = []
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Categorised {len(rows)} transactions. Wrote {args.output_csv}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
