"""Tests for the invoice-aging-v2 fixture.

On a clean clone (no fix applied):

    cd fixtures/broken_agents/invoice_aging_v2
    python3 -m pytest tests/ -q

→ ``test_boundary_31_days_in_31_60_bucket`` fails and
  ``test_output_matches_expected_output_csv`` fails (because every
  invoice that is exactly 31 days overdue lands in the ``1-30`` bucket
  instead of ``31-60``). 5 tests pass, 2 fail.

After the repair workflow applies the canonical one-line fix (change
``<= 31`` to ``<= 30`` in :func:`agent.categorise`), all 7 tests pass.
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

INPUT_CSV = DATA_DIR / "input_invoices.csv"
EXPECTED_CSV = DATA_DIR / "expected_output.csv"

ALLOWED_BUCKETS = {"Paid", "Current", "1-30", "31-60", "61-90", "90+"}
ALLOWED_RISK_FLAGS = {"none", "low", "medium", "high"}


def run_agent(output_csv: Path) -> None:
    """Execute ``agent.py`` against the bundled sample input."""
    result = subprocess.run(
        [sys.executable, str(AGENT), str(INPUT_CSV), str(output_csv)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"agent.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Tests that PASS even with the bug
# ---------------------------------------------------------------------------


def test_output_schema_includes_expected_columns(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(out)
    rows = read_csv(out)
    assert rows
    expected_cols = {
        "invoice_id", "customer", "invoice_date", "due_date",
        "amount", "status", "days_overdue", "aging_bucket", "risk_flag",
    }
    assert expected_cols.issubset(set(rows[0].keys())), (
        f"missing columns: {expected_cols - set(rows[0].keys())}"
    )


def test_row_count_preserved(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(out)
    assert len(read_csv(out)) == len(read_csv(INPUT_CSV))


def test_paid_invoices_marked_paid(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(out)
    paid_rows = [r for r in read_csv(out) if r["status"].lower() == "paid"]
    assert paid_rows, "expected at least one paid invoice in the sample"
    for r in paid_rows:
        assert r["aging_bucket"] == "Paid", (
            f"{r['invoice_id']}: paid invoices must be tagged Paid, got "
            f"{r['aging_bucket']!r}"
        )


def test_bucket_and_risk_values_are_in_allowed_enum(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(out)
    for r in read_csv(out):
        assert r["aging_bucket"] in ALLOWED_BUCKETS, (
            f"{r['invoice_id']}: bucket {r['aging_bucket']!r} not in {ALLOWED_BUCKETS}"
        )
        assert r["risk_flag"] in ALLOWED_RISK_FLAGS, (
            f"{r['invoice_id']}: risk_flag {r['risk_flag']!r} not in {ALLOWED_RISK_FLAGS}"
        )


def test_high_amount_over_90_days_flagged_high_risk(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(out)
    sample = {
        r["invoice_id"]: r
        for r in read_csv(out)
        if r["aging_bucket"] == "90+" and float(r["amount"]) >= 1000
    }
    assert sample, "expected at least one 90+ invoice with amount ≥ 1000"
    for r in sample.values():
        assert r["risk_flag"] == "high", (
            f"{r['invoice_id']} ({r['amount']}, 90+) should be high risk, "
            f"got {r['risk_flag']!r}"
        )


# ---------------------------------------------------------------------------
# Tests that FAIL with the boundary bug
# ---------------------------------------------------------------------------


def test_boundary_31_days_in_31_60_bucket(tmp_path: Path) -> None:
    """The load-bearing failing test.

    Invoices that are EXACTLY 31 days overdue must be placed in the
    ``31-60`` aging bucket, not ``1-30``. The bundled sample has three
    such invoices (INV-0005, INV-0013, INV-0018); a correct agent
    routes all three to ``31-60``.
    """
    out = tmp_path / "out.csv"
    run_agent(out)
    rows = read_csv(out)
    boundary_rows = [r for r in rows if r["days_overdue"] == "31"]
    assert len(boundary_rows) >= 1, (
        "expected at least one invoice with days_overdue == 31 in the sample"
    )
    misbucketed = [r for r in boundary_rows if r["aging_bucket"] != "31-60"]
    assert not misbucketed, (
        "Invoices exactly 31 days overdue must land in the 31-60 bucket. "
        "Examples:\n"
        + "\n".join(
            f"  {r['invoice_id']}: days_overdue={r['days_overdue']}, "
            f"bucket={r['aging_bucket']!r}"
            for r in misbucketed[:5]
        )
    )


def test_output_matches_expected_output_csv(tmp_path: Path) -> None:
    """Row-aligned comparison against the committed expected output."""
    out = tmp_path / "out.csv"
    run_agent(out)
    actual = {r["invoice_id"]: r for r in read_csv(out)}
    expected = {r["invoice_id"]: r for r in read_csv(EXPECTED_CSV)}
    assert set(actual) == set(expected), (
        f"row id mismatch: missing={set(expected) - set(actual)} "
        f"unexpected={set(actual) - set(expected)}"
    )
    drifts: list[str] = []
    for inv_id, exp in expected.items():
        act = actual[inv_id]
        for col in ("days_overdue", "aging_bucket", "risk_flag"):
            if act[col] != exp[col]:
                drifts.append(
                    f"  {inv_id}/{col}: expected={exp[col]!r} actual={act[col]!r}"
                )
    assert not drifts, "Output drifts from expected_output.csv:\n" + "\n".join(drifts[:10])
