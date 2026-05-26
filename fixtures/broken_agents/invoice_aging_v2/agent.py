"""Invoice aging agent (v2 — richer schema, boundary bug).

Reads a CSV of invoices and produces an aging report by computing
``days_overdue = today - due_date`` and bucketing each row.

Schema
------

Input columns
    invoice_id, customer, invoice_date, due_date, amount, status

Output columns (input + 3 new)
    invoice_id, customer, invoice_date, due_date, amount, status,
    days_overdue, aging_bucket, risk_flag

Buckets
    Paid     — status == "paid"
    Current  — not yet due (days_overdue < 0)
    1-30     — 1 ≤ days_overdue ≤ 30
    31-60    — 31 ≤ days_overdue ≤ 60
    61-90    — 61 ≤ days_overdue ≤ 90
    90+      — days_overdue > 90

Risk flags
    none       — Paid or Current
    low        — 1-30
    medium     — 31-60, 61-90, or 90+ below high-risk threshold
    high       — 90+ AND amount ≥ 1000

Usage
    python agent.py <input_csv> <output_csv>

NOTE FOR REVIEWERS: this agent ships with a deliberate boundary bug.
The repair workflow's job is to reproduce, diagnose, fix, and validate
it. The intentional bug is documented in ``data/problem_report.md``
from the AR clerk's point of view.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

# "Today" is fixed so aging buckets are stable across runs (a normal
# pattern for nightly AR reports run from a snapshot).
DEFAULT_TODAY = date(2026, 5, 1)

HIGH_RISK_AMOUNT_THRESHOLD = 1000.00


def parse_date(date_str: str) -> date:
    """Parse an ISO-format date string (YYYY-MM-DD)."""
    return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()


def categorise(*, status: str, days_overdue: int, amount: float) -> tuple[str, str]:
    """Return ``(aging_bucket, risk_flag)`` for one invoice.

    Pure function; first-match-wins cascade.
    """
    if status.strip().lower() == "paid":
        return ("Paid", "none")
    if days_overdue < 0:
        return ("Current", "none")
    # BOUNDARY BUG: the upper bound below should be ``<= 30``, not
    # ``<= 31``. With ``<= 31``, invoices that are exactly 31 days
    # overdue land in "1-30" instead of "31-60".
    if days_overdue <= 31:
        return ("1-30", "low")
    if days_overdue <= 60:
        return ("31-60", "medium")
    if days_overdue <= 90:
        return ("61-90", "medium")
    # 90+ overdue → high risk only when the unpaid amount is sizeable.
    if amount >= HIGH_RISK_AMOUNT_THRESHOLD:
        return ("90+", "high")
    return ("90+", "medium")


def process_row(row: dict[str, str], *, today: date) -> dict[str, str]:
    """Compute days_overdue + aging_bucket + risk_flag for one row."""
    due = parse_date(row["due_date"])
    days_overdue = (today - due).days
    try:
        amount = float(row.get("amount") or "0")
    except ValueError:
        amount = 0.0
    bucket, risk = categorise(
        status=row.get("status", ""), days_overdue=days_overdue, amount=amount
    )
    out = dict(row)
    out["days_overdue"] = str(days_overdue)
    out["aging_bucket"] = bucket
    out["risk_flag"] = risk
    return out


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
        rows = [process_row(dict(r), today=today) for r in csv.DictReader(fin)]

    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = [
            "invoice_id", "customer", "invoice_date", "due_date",
            "amount", "status", "days_overdue", "aging_bucket", "risk_flag",
        ]
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Processed {len(rows)} invoices. Wrote {args.output_csv}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
