"""Tests for the bank-categoriser template.

These tests run from the unpacked archive (the orchestrator stages
``generated/data/{sample_input,golden_output}.csv``) as well as from
the in-repo template. They assert the **final schema** the workflow
contract promises:

  * all original input columns preserved
  * ``category`` exists and is in the allowed enum
  * ``rule_matched`` exists, non-null
  * ``rule_used`` exists, non-null
  * ``confidence`` exists, non-null, in ``[0, 1]``
  * row count preserved
  * refund override fires on negative amounts
  * description patterns map to the documented categories
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE_ROOT = HERE.parent
DATA_DIR = TEMPLATE_ROOT / "data"
AGENT = TEMPLATE_ROOT / "agent.py"

ALLOWED_CATEGORIES = {
    "Income",
    "Office Expense",
    "Travel",
    "Subscriptions",
    "Refund",
    "Uncategorised",
}

REQUIRED_OUTPUT_COLUMNS = ("category", "rule_matched", "rule_used", "confidence")
INPUT_COLUMNS = ("txn_id", "date", "amount", "description", "counterparty", "account")


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


# ---------------------------------------------------------------------------
# Schema preservation
# ---------------------------------------------------------------------------


def test_output_schema_includes_all_required_columns(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    rows = read_csv(out)
    assert rows, "expected at least one row"
    keys = set(rows[0].keys())
    for col in INPUT_COLUMNS:
        assert col in keys, f"input column {col!r} missing in output"
    for col in REQUIRED_OUTPUT_COLUMNS:
        assert col in keys, f"required output column {col!r} missing"


def test_row_count_preserved(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    actual = read_csv(out)
    inputs = read_csv(DATA_DIR / "sample_input.csv")
    assert len(actual) == len(inputs), (
        f"row count drift: actual={len(actual)}, input={len(inputs)}"
    )


# ---------------------------------------------------------------------------
# Per-column invariants
# ---------------------------------------------------------------------------


def test_category_enum_membership(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    for row in read_csv(out):
        cat = row["category"]
        assert cat, f"empty category on {row['txn_id']}"
        assert cat in ALLOWED_CATEGORIES, (
            f"unknown category {cat!r} on {row['txn_id']}; "
            f"allowed={sorted(ALLOWED_CATEGORIES)}"
        )


def test_rule_used_non_null(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    for row in read_csv(out):
        assert row.get("rule_used"), f"empty rule_used on {row['txn_id']}"
        # rule_used mirrors rule_matched for this template.
        assert row["rule_used"] == row["rule_matched"], (
            f"{row['txn_id']}: rule_used={row['rule_used']!r} != "
            f"rule_matched={row['rule_matched']!r}"
        )


def test_confidence_is_valid_float_in_unit_interval(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    for row in read_csv(out):
        raw = row.get("confidence")
        assert raw not in (None, ""), f"empty confidence on {row['txn_id']}"
        c = float(raw)
        assert 0.0 <= c <= 1.0, (
            f"confidence {c} out of [0,1] on {row['txn_id']}"
        )


# ---------------------------------------------------------------------------
# Semantic rule behaviour
# ---------------------------------------------------------------------------


def _write_input(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    p = tmp_path / "in.csv"
    with p.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(INPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return p


def _categorise(tmp_path: Path, row: dict[str, str]) -> dict[str, str]:
    inp = _write_input(tmp_path, [row])
    out = tmp_path / "out.csv"
    run_agent(inp, out)
    return read_csv(out)[0]


def test_negative_amount_overrides_to_refund(tmp_path: Path) -> None:
    """Refund-first override: amount < 0 → Refund regardless of description."""
    result = _categorise(
        tmp_path,
        {
            "txn_id": "T-refund",
            "date": "2026-01-15",
            "amount": "-145.00",
            "description": "AWS REFUND CREDIT",  # would otherwise match Subscriptions
            "counterparty": "AWS",
            "account": "ACC-001",
        },
    )
    assert result["category"] == "Refund"
    assert result["rule_matched"] == "negative_amount"
    assert result["rule_used"] == "negative_amount"
    assert float(result["confidence"]) == 1.00


def test_subscription_pattern(tmp_path: Path) -> None:
    result = _categorise(
        tmp_path,
        {
            "txn_id": "T-sub",
            "date": "2026-02-01",
            "amount": "29.99",
            "description": "GITHUB MONTHLY PLAN",
            "counterparty": "GitHub",
            "account": "ACC-001",
        },
    )
    assert result["category"] == "Subscriptions"
    assert result["rule_matched"] == "subscription_pattern"
    assert float(result["confidence"]) == 0.95


def test_travel_pattern(tmp_path: Path) -> None:
    result = _categorise(
        tmp_path,
        {
            "txn_id": "T-travel",
            "date": "2026-02-02",
            "amount": "78.00",
            "description": "UBER TRIP 6th AVE",
            "counterparty": "Uber",
            "account": "ACC-002",
        },
    )
    assert result["category"] == "Travel"
    assert result["rule_matched"] == "travel_pattern"


def test_office_expense_pattern(tmp_path: Path) -> None:
    result = _categorise(
        tmp_path,
        {
            "txn_id": "T-office",
            "date": "2026-02-03",
            "amount": "42.10",
            "description": "STAPLES OFFICE SUPPLIES",
            "counterparty": "Staples",
            "account": "ACC-002",
        },
    )
    assert result["category"] == "Office Expense"
    assert result["rule_matched"] == "office_pattern"


def test_income_pattern(tmp_path: Path) -> None:
    result = _categorise(
        tmp_path,
        {
            "txn_id": "T-income",
            "date": "2026-02-04",
            "amount": "4200.00",
            "description": "MONTHLY SALARY PAYROLL",
            "counterparty": "Employer",
            "account": "ACC-001",
        },
    )
    assert result["category"] == "Income"
    assert result["rule_matched"] == "income_pattern"


def test_uncategorised_fallback(tmp_path: Path) -> None:
    result = _categorise(
        tmp_path,
        {
            "txn_id": "T-unk",
            "date": "2026-02-05",
            "amount": "60.00",
            "description": "ANONYMOUS TRANSFER",
            "counterparty": "Anon Ltd",
            "account": "ACC-001",
        },
    )
    assert result["category"] == "Uncategorised"
    assert result["rule_matched"] == "no_match"
    assert float(result["confidence"]) == 0.00


def test_vendor_map_hit_lower_confidence(tmp_path: Path) -> None:
    """When description doesn't match a regex but counterparty maps."""
    result = _categorise(
        tmp_path,
        {
            "txn_id": "T-vendor",
            "date": "2026-02-06",
            "amount": "120.00",
            "description": "PURCHASE",
            "counterparty": "Dell",
            "account": "ACC-002",
        },
    )
    assert result["category"] == "Office Expense"
    assert result["rule_matched"].startswith("vendor_map:")
    assert float(result["confidence"]) == 0.70


# ---------------------------------------------------------------------------
# Golden parity (regression — output must equal the committed golden)
# ---------------------------------------------------------------------------


def test_happy_path_matches_golden(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    run_agent(DATA_DIR / "sample_input.csv", out)
    actual = read_csv(out)
    golden = read_csv(DATA_DIR / "golden_output.csv")
    assert len(actual) == len(golden)
    actual_by_id = {r["txn_id"]: r for r in actual}
    drifts: list[str] = []
    for g in golden:
        a = actual_by_id[g["txn_id"]]
        for col in ("category", "rule_matched", "rule_used", "confidence"):
            if a.get(col) != g.get(col):
                drifts.append(
                    f"{g['txn_id']}/{col}: {g.get(col)!r} → {a.get(col)!r}"
                )
    assert not drifts, "Output drifts from golden:\n" + "\n".join(drifts[:20])
