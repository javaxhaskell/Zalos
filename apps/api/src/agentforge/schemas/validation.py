"""Validation report schemas for author and repair flows."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from agentforge.schemas.common import StrictModel


class ValidationLayer(StrEnum):
    """Author-flow validation layers (ADR-0007).

    The original six layers (1-6) are the contract. Auxiliary layers
    (template_reference, semantic_rules) are emitted alongside when the
    workflow has the inputs for them; they never replace the six.
    """

    SCHEMA = "schema"
    REQUIRED_COLUMNS = "required_columns"
    BUSINESS_RULES = "business_rules"
    ROW_LEVEL = "row_level"
    GOLDEN_OUTPUT = "golden_output"
    GENERATED_PYTEST = "generated_pytest"
    TEMPLATE_REFERENCE = "template_reference"
    SEMANTIC_RULES = "semantic_rules"


class ValidationCheck(StrictModel):
    """One layer's outcome.

    Three terminal states, encoded as ``(passed, skipped)``:

      * ``(True, False)``  — passed.
      * ``(False, False)`` — failed.
      * ``(None, True)``   — skipped (inputs absent). ``passed`` is
        ``null`` in JSON; the markdown renderer prints ``SKIPPED``;
        ``overall_passed`` ignores skipped layers either way.

    The deliberate ``None`` means a JSON consumer cannot read a
    skipped layer as a passing layer just by checking ``passed ==
    True``.
    """

    layer: ValidationLayer
    name: str
    """Humanised check name shown to the user."""
    passed: bool | None
    """``None`` iff ``skipped`` is True."""
    evidence: str
    """One-line evidence (e.g., '200/200 rows have a category')."""
    detail: str | None = None
    """Optional expandable detail (collapsed by default in UI)."""
    hint_if_failed: str | None = None
    skipped: bool = False
    """``True`` if this check was not performed (inputs absent)."""


class ValidationReport(StrictModel):
    """Bundle of layer outcomes; rendered as markdown + JSON sidecar."""

    session_id: UUID
    generated_at: datetime
    overall_passed: bool
    checks: list[ValidationCheck]
    """One per layer (or per check if a layer produces multiple)."""


# ---------------------------------------------------------------------------
# Repair report
# ---------------------------------------------------------------------------


class FilesChangedEntry(StrictModel):
    """One file changed by the repair patch."""

    file: str
    hunks_count: int
    diff_hash: str
    """sha256 of the patch text."""
    summary: str


class TestRunSummary(StrictModel):
    """pytest before/after summary."""

    passed_count: int
    failed_count: int
    total_count: int
    failing_tests: list[str] = Field(default_factory=list)


class RepairReport(StrictModel):
    """Six-section structured repair report.

    Rendered to ``reports/repair_report.md`` plus JSON sidecar.
    """

    session_id: UUID
    generated_at: datetime
    problem: str
    """User's verbatim problem statement."""
    reproduction: str
    """Plain-English summary of the reproduction observation."""
    diagnosis: str
    """Plain-English root cause naming file and approximate line."""
    files_changed: list[FilesChangedEntry]
    validation_before: TestRunSummary
    validation_after: TestRunSummary
    golden_diff_zero: bool | None = None
    """True if a golden ``expected_output.csv`` was present and matched exactly."""
    remaining_risks: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
