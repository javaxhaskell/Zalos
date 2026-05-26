"""Invoice aging agent.

Reads a CSV of invoices and produces an aging report by computing the age of
each invoice in days and bucketing it into one of {0-30, 31-60, 61-90, 90+}.

Usage:
    python agent.py <input_csv> <output_csv>

NOTE FOR REVIEWERS: this agent ships with a known issue (the agent is a
deliberately broken fixture for AgentForge's repair workflow). The fix lands
in the repair report once a session completes; do not patch it directly.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

from rules import compute_aging_bucket

# "Today" is fixed to a known reference date so aging buckets are stable
# across runs (a common pattern for batch-processed AR reports).
DEFAULT_TODAY = date(2026, 4, 30)


def parse_invoice_date(date_str: str) -> date:
    """Parse an invoice date string into a ``date``.

    NOTE: assumes DD-MM-YYYY input format.
    """
    return datetime.strptime(date_str, "%d-%m-%Y").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute invoice aging buckets.")
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    args = parser.parse_args(argv)

    if not args.input_csv.exists():
        print(f"error: input not found: {args.input_csv}", file=sys.stderr)
        return 2

    today = DEFAULT_TODAY

    with args.input_csv.open(newline="") as fin:
        rows = [dict(r) for r in csv.DictReader(fin)]

    for row in rows:
        try:
            inv_date = parse_invoice_date(row["invoice_date"])
            age_days = (today - inv_date).days
            row["age_days"] = str(age_days)
            row["aging_bucket"] = compute_aging_bucket(age_days)
        except ValueError:
            # Date couldn't be parsed; tag the row so downstream can triage.
            row["age_days"] = ""
            row["aging_bucket"] = "PARSE_ERROR"

    fieldnames = list(rows[0].keys()) if rows else []
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Processed {len(rows)} invoices. Wrote {args.output_csv}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
