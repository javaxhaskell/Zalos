"""Unit tests for the repair-report renderer + assembly helpers (BP6).

Covers the deterministic pieces of the RepairReport pipeline that don't
need an end-to-end run: the markdown render against a hand-built
report, the FilesChangedEntry assembly with sha256 hashing, and the
golden-diff-zero extraction from a ValidationCheck list.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from agentforge.schemas import (
    FilesChangedEntry,
    RepairReport,
    ValidationCheck,
    ValidationLayer,
)
from agentforge.schemas import TestRunSummary as RunSummary
from agentforge.validation import (
    assemble_files_changed,
    golden_diff_zero_from_validation_checks,
    render_repair_markdown,
)


def _sample_report() -> RepairReport:
    return RepairReport(
        session_id=uuid4(),
        generated_at=datetime.now(UTC),
        problem="April invoices end up in the wrong aging bucket.",
        reproduction="pytest tests/test_aging.py::test_april_invoices_in_first_bucket fails: April rows mis-bucketed.",
        diagnosis="working/agent.py line 34 uses '%d-%m-%Y' but sample is MM-DD-YYYY.",
        files_changed=[
            FilesChangedEntry(
                file="working/agent.py",
                hunks_count=1,
                diff_hash="a" * 64,
                summary="Flip strptime format to %m-%d-%Y.",
            )
        ],
        validation_before=RunSummary(
            passed_count=2,
            failed_count=1,
            total_count=3,
            failing_tests=["tests/test_aging.py::test_april_invoices_in_first_bucket"],
        ),
        validation_after=RunSummary(
            passed_count=3,
            failed_count=0,
            total_count=3,
        ),
        golden_diff_zero=True,
        remaining_risks=["Date format inference is heuristic — surface to user on new uploads"],
        next_steps=["Add an inspect_csv_schema ambiguity_note check before run"],
    )


def test_render_repair_markdown_contains_six_sections() -> None:
    md = render_repair_markdown(_sample_report())
    for heading in (
        "## 1. Problem statement",
        "## 2. Reproduction",
        "## 3. Diagnosis",
        "## 4. Files changed",
        "## 5. Validation",
        "## 6. Remaining risks and next steps",
    ):
        assert heading in md, f"missing heading: {heading}"


def test_render_repair_markdown_quotes_verbatim_problem() -> None:
    md = render_repair_markdown(_sample_report())
    assert "> April invoices end up in the wrong aging bucket." in md


def test_render_repair_markdown_surfaces_files_changed() -> None:
    md = render_repair_markdown(_sample_report())
    assert "**working/agent.py**" in md
    assert "1 hunk(s)" in md
    assert "Flip strptime format" in md


def test_render_repair_markdown_renders_before_after_test_summaries() -> None:
    md = render_repair_markdown(_sample_report())
    assert "**Before fix**: 2/3 passed, 1 failed" in md
    assert "**After fix**: 3/3 passed, 0 failed" in md


def test_render_repair_markdown_marks_golden_pass() -> None:
    md = render_repair_markdown(_sample_report())
    assert "Golden output comparison: **PASS**" in md


def test_render_repair_markdown_handles_no_golden() -> None:
    report = _sample_report()
    report = report.model_copy(update={"golden_diff_zero": None})
    md = render_repair_markdown(report)
    assert "Golden output comparison: not run" in md


def test_assemble_files_changed_hashes_the_diff() -> None:
    diff = "--- a/x.py\n+++ b/x.py\n@@\n-old\n+new\n"
    entry = assemble_files_changed(
        file_path="x.py", unified_diff=diff, hunks_count=1, summary="ok"
    )
    expected_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    assert entry.diff_hash == expected_hash
    assert entry.hunks_count == 1


def test_golden_diff_zero_from_validation_checks_pass() -> None:
    checks = [
        ValidationCheck(
            layer=ValidationLayer.SCHEMA, name="s", passed=True, evidence="ok"
        ),
        ValidationCheck(
            layer=ValidationLayer.GOLDEN_OUTPUT,
            name="golden",
            passed=True,
            evidence="200/200 matched",
        ),
    ]
    assert golden_diff_zero_from_validation_checks(checks) is True


def test_golden_diff_zero_from_validation_checks_fail() -> None:
    checks = [
        ValidationCheck(
            layer=ValidationLayer.GOLDEN_OUTPUT,
            name="golden",
            passed=False,
            evidence="3 cell mismatches",
        ),
    ]
    assert golden_diff_zero_from_validation_checks(checks) is False


def test_golden_diff_zero_from_validation_checks_skipped() -> None:
    checks = [
        ValidationCheck(
            layer=ValidationLayer.GOLDEN_OUTPUT,
            name="golden",
            passed=True,
            evidence="no golden CSV staged; layer skipped",
        ),
    ]
    assert golden_diff_zero_from_validation_checks(checks) is None


def test_golden_diff_zero_from_validation_checks_absent_layer() -> None:
    checks = [
        ValidationCheck(
            layer=ValidationLayer.SCHEMA, name="s", passed=True, evidence="ok"
        ),
    ]
    assert golden_diff_zero_from_validation_checks(checks) is None
