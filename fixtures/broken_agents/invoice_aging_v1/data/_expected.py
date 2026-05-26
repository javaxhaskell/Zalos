"""Generate expected_output.csv — what a CORRECT agent would produce.

Run from this directory:

    cd fixtures/broken_agents/invoice_aging_v1/data && python _expected.py

This script parses dates with the CORRECT format (MM-DD-YYYY) and applies
the same bucketing logic as rules.py. The result is the file the buggy
``agent.py`` is supposed to produce after the repair workflow fixes the bug.
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime
from pathlib import Path

# Make rules.py importable
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rules import compute_aging_bucket  # noqa: E402

TODAY = date(2026, 4, 30)

with (HERE / "sample_input.csv").open(newline="") as f:
    rows = list(csv.DictReader(f))

for row in rows:
    # Correct format: MM-DD-YYYY
    inv_date = datetime.strptime(row["invoice_date"], "%m-%d-%Y").date()
    age = (TODAY - inv_date).days
    row["age_days"] = str(age)
    row["aging_bucket"] = compute_aging_bucket(age)

fieldnames = list(rows[0].keys())
with (HERE / "expected_output.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

print(f"Wrote {len(rows)} rows to expected_output.csv")
