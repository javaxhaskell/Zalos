"""Tests for the invoice aging fixture.

Three checks:
  - test_aging_buckets_are_valid_strings: every output row's bucket is a
    member of the allowed set. PASSES with the bug because PARSE_ERROR is
    in the allowed set.
  - test_amount_preserved: amounts in output equal amounts in input. PASSES
    with the bug because the bug only affects dates.
  - test_april_invoices_in_first_bucket: invoices dated April 2026 (today
    is 2026-04-30) should land in the 0-30 bucket. FAILS with the bug
    because some April rows raise ValueError and others silently misparse.

Expected outcome on a clean clone:
    pytest tests/test_aging.py -q  →  2 passed, 1 failed
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_ROOT = HERE.parent
DATA_DIR = FIXTURE_ROOT / "data"
AGENT = FIXTURE_ROOT / "agent.py"

VALID_BUCKETS = {"0-30", "31-60", "61-90", "90+", "PARSE_ERROR"}


def run_agent(input_csv: Path, output_csv: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(AGENT), str(input_csv), str(output_csv)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"agent.py exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def test_aging_buckets_are_valid_strings(tmp_path: Path) -> None:
    """Every output row's aging_bucket is one of the allowed strings."""
    output = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", output)
    rows = read_csv(output)
    for r in rows:
        assert r["aging_bucket"] in VALID_BUCKETS, (
            f"unknown bucket {r['aging_bucket']!r} on row {r['invoice_id']}"
        )


def test_amount_preserved(tmp_path: Path) -> None:
    """Amounts in the output equal amounts in the input (date bug shouldn't affect amounts)."""
    output = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", output)
    input_rows = read_csv(DATA_DIR / "sample_input.csv")
    output_rows = read_csv(output)
    assert len(input_rows) == len(output_rows)
    by_id = {r["invoice_id"]: r for r in output_rows}
    for r in input_rows:
        assert by_id[r["invoice_id"]]["amount"] == r["amount"], (
            f"amount drift on {r['invoice_id']}"
        )


def test_april_invoices_in_first_bucket(tmp_path: Path) -> None:
    """April 2026 invoices (today is 2026-04-30) should be in the 0-30 bucket."""
    output = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", output)
    rows = read_csv(output)
    april_rows = [r for r in rows if r["invoice_id"].startswith("INV-2026-04-")]
    assert len(april_rows) >= 5, "expected at least 5 April invoices in sample"
    misbucketed = [
        r for r in april_rows if r["aging_bucket"] != "0-30"
    ]
    assert not misbucketed, (
        "Some April invoices are not in 0-30. Examples:\n"
        + "\n".join(
            f"  {r['invoice_id']}: bucket={r['aging_bucket']!r}, age_days={r['age_days']!r}"
            for r in misbucketed[:5]
        )
    )
