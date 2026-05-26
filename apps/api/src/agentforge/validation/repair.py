"""Repair-report renderer + 6-piece assembly (ADR-0007 §RepairReport).

The six pieces of repair evidence per ADR-0007:

  1. **Reproduction** — `ReproductionResult` showing the bug manifests.
  2. **Failing test / wrong output** — specific test name + excerpt.
  3. **Root cause** — `Diagnosis` naming file + line + explanation.
  4. **Patch** — unified diff + rationale.
  5. **After-fix run** — same pytest invocation showing it passes.
  6. **Expected-output comparison** — golden diff against the fixture's
     ``expected_output.csv`` (if present).

These pieces accumulate as events during the repair flow; this module
gathers them into a typed :class:`RepairReport` and renders the
markdown that lands at ``reports/repair_report.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentforge.schemas import (
    FilesChangedEntry,
    RepairReport,
    TestRunSummary,
    ValidationCheck,
)


def render_repair_markdown(report: RepairReport) -> str:
    """Render the canonical six-section repair report as markdown.

    Format matches ``CONTRACTS.md`` §7. The structure is fixed so a
    finance user (and downstream change-management operators) see the
    same shape every time.
    """
    lines: list[str] = []
    lines.append(f"# Repair Report — Session {report.session_id}")
    lines.append("")
    lines.append(f"Generated: {report.generated_at.isoformat()}")
    lines.append("")

    # 1. Problem statement
    lines.append("## 1. Problem statement")
    lines.append("")
    lines.append("> " + report.problem.replace("\n", "\n> "))
    lines.append("")

    # 2. Reproduction
    lines.append("## 2. Reproduction")
    lines.append("")
    lines.append(report.reproduction)
    lines.append("")

    # 3. Diagnosis
    lines.append("## 3. Diagnosis")
    lines.append("")
    lines.append(report.diagnosis)
    lines.append("")

    # 4. Files changed
    lines.append("## 4. Files changed")
    lines.append("")
    if not report.files_changed:
        lines.append("_(no files changed)_")
    else:
        for entry in report.files_changed:
            lines.append(f"- **{entry.file}** — {entry.hunks_count} hunk(s)")
            lines.append(f"  - diff hash: `{entry.diff_hash[:16]}…`")
            if entry.summary:
                lines.append(f"  - summary: {entry.summary}")
    lines.append("")

    # 5. Validation (before / after / golden diff)
    lines.append("## 5. Validation")
    lines.append("")
    lines.append(_render_test_summary("Before fix", report.validation_before))
    lines.append("")
    lines.append(_render_test_summary("After fix", report.validation_after))
    lines.append("")
    if report.golden_diff_zero is True:
        lines.append(
            "- Golden output comparison: **PASS** (zero divergence "
            "from expected_output.csv)"
        )
    elif report.golden_diff_zero is False:
        lines.append(
            "- Golden output comparison: **FAIL** (output diverged "
            "from expected_output.csv)"
        )
    else:
        lines.append("- Golden output comparison: not run (no expected_output.csv staged)")
    lines.append("")

    # 6. Remaining risks + next steps
    lines.append("## 6. Remaining risks and next steps")
    lines.append("")
    if report.remaining_risks:
        lines.append("**Remaining risks**")
        for risk in report.remaining_risks:
            lines.append(f"- {risk}")
        lines.append("")
    if report.next_steps:
        lines.append("**Next steps**")
        for step in report.next_steps:
            lines.append(f"- {step}")
        lines.append("")
    if not report.remaining_risks and not report.next_steps:
        lines.append("_(none identified)_")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_test_summary(label: str, summary: TestRunSummary) -> str:
    head = (
        f"- **{label}**: {summary.passed_count}/{summary.total_count} passed"
        f", {summary.failed_count} failed"
    )
    if summary.failing_tests:
        head += "\n  - Failing tests:"
        for name in summary.failing_tests[:10]:
            head += f"\n    - `{name}`"
    return head


# ---------------------------------------------------------------------------
# 6-piece evidence assembly
# ---------------------------------------------------------------------------


def assemble_files_changed(
    *,
    file_path: str,
    unified_diff: str,
    hunks_count: int,
    summary: str = "",
) -> FilesChangedEntry:
    """Build a :class:`FilesChangedEntry` from the applied-patch outcome.

    Currently single-file; multi-file repairs would call this once per
    file. The ``diff_hash`` is sha256 of the diff text so a downstream
    auditor can verify the recorded diff matches the on-disk patch.
    """
    import hashlib

    return FilesChangedEntry(
        file=file_path,
        hunks_count=hunks_count,
        diff_hash=hashlib.sha256(unified_diff.encode("utf-8")).hexdigest(),
        summary=summary,
    )


def golden_diff_zero_from_validation_checks(
    checks: Sequence[ValidationCheck],
) -> bool | None:
    """Extract the golden-diff outcome from a list of validation checks.

    Returns:
      * ``True`` if the golden-output layer ran and passed.
      * ``False`` if it ran and failed.
      * ``None`` if it was skipped (no golden staged).
    """
    from agentforge.schemas import ValidationLayer

    for check in checks:
        if check.layer != ValidationLayer.GOLDEN_OUTPUT:
            continue
        if "skipped" in check.evidence.lower():
            return None
        return check.passed
    return None


__all__ = [
    "assemble_files_changed",
    "golden_diff_zero_from_validation_checks",
    "render_repair_markdown",
]
