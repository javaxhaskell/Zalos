"""Regression for the ``invoice_aging_v2`` broken-agent fixture.

Two independent properties must hold for the take-home Repair
workflow to be a faithful prototype:

  * **The fixture must genuinely fail before any repair.** Running
    its bundled pytest suite from a clean clone must surface exactly
    the documented boundary failure — no fake or always-passing
    tests.

  * **The canonical one-line fix must make every bundled test
    pass.** This proves the repair is real: applying the documented
    patch (``<= 31`` → ``<= 30`` in :func:`agent.categorise`) is
    sufficient.

Both tests run the fixture's pytest suite via subprocess against a
disposable copy of the fixture in ``tmp_path`` so the source tree on
disk stays in its broken state (which is the load-bearing property
of a "broken fixture").
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_SRC = _REPO_ROOT / "fixtures" / "broken_agents" / "invoice_aging_v2"


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the fixture into a disposable directory."""
    dest = tmp_path / "invoice_aging_v2"
    shutil.copytree(
        FIXTURE_SRC,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    return dest


def _run_pytest(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_fixture_fails_before_repair(tmp_path: Path) -> None:
    """Reproduce the bug: 2 expected failures + 5 passes."""
    copy = _copy_fixture(tmp_path)
    result = _run_pytest(copy)
    output = result.stdout + result.stderr

    # The fixture must report a non-zero exit (pytest's failure code).
    assert result.returncode != 0, (
        "Fixture is supposed to fail before repair; pytest exited 0.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # The two documented failing tests must be exactly the load-bearing
    # ones (we don't want a fake "always-failing" test masking a real
    # check).
    assert "test_boundary_31_days_in_31_60_bucket" in output
    assert "test_output_matches_expected_output_csv" in output
    assert "2 failed, 5 passed" in output or "5 passed, 2 failed" in output, (
        f"expected 5/2 split, got pytest summary:\n{output[-2000:]}"
    )


def test_canonical_fix_makes_fixture_pass(tmp_path: Path) -> None:
    """Applying the documented one-line patch must make every test pass."""
    copy = _copy_fixture(tmp_path)
    agent_py = copy / "agent.py"
    original = agent_py.read_text(encoding="utf-8")
    assert "if days_overdue <= 31:" in original, (
        "Fixture is no longer in its broken state — the boundary "
        "comparison must read 'if days_overdue <= 31:' before repair."
    )

    fixed = original.replace(
        "if days_overdue <= 31:",
        "if days_overdue <= 30:",
        1,
    )
    assert fixed != original, "canonical patch had no effect"
    agent_py.write_text(fixed, encoding="utf-8")

    result = _run_pytest(copy)
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        "Canonical fix did not make every test pass.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "7 passed" in output, (
        f"expected 7 passing tests after fix; got:\n{output[-2000:]}"
    )


def test_fixture_has_all_required_metadata_files() -> None:
    """The take-home brief requires problem_report.md + tests + data."""
    required = [
        "README.md",
        "problem_report.md",
        "requirements.txt",
        "agent.py",
        "data/input_invoices.csv",
        "data/expected_output.csv",
        "tests/test_agent.py",
    ]
    missing = [r for r in required if not (FIXTURE_SRC / r).is_file()]
    assert not missing, f"fixture is missing: {missing}"


@pytest.mark.parametrize(
    "boundary_invoice_id",
    ["INV-0005", "INV-0013", "INV-0018"],
)
def test_expected_output_assigns_boundary_invoices_to_31_60(
    boundary_invoice_id: str,
) -> None:
    """Sanity-check the committed expected_output.csv itself:
    each 31-day-overdue invoice belongs in the 31-60 bucket. This
    keeps the golden honest if anyone ever edits it carelessly."""
    import csv

    expected = FIXTURE_SRC / "data" / "expected_output.csv"
    with expected.open() as f:
        rows = {r["invoice_id"]: r for r in csv.DictReader(f)}
    row = rows[boundary_invoice_id]
    assert row["days_overdue"] == "31"
    assert row["aging_bucket"] == "31-60"
    assert row["risk_flag"] == "medium"
