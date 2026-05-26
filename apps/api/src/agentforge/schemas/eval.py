"""Evaluation scenario and run-result schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field

from agentforge.schemas.common import SessionStatus, StrictModel


class EvalKind(StrEnum):
    AUTHOR = "author"
    REPAIR = "repair"
    ADVERSARIAL = "adversarial"


class GoldenOutputSpec(StrictModel):
    """How an output file should compare to the committed golden."""

    path: str
    """Relative to the session workspace (e.g., 'outputs/categorised_transactions.csv')."""
    golden: str
    """Relative to repo root (e.g., 'evals/golden/bank_categoriser/golden_output.csv')."""
    comparison: Literal["row_aligned_on_primary_key", "exact"] = (
        "row_aligned_on_primary_key"
    )
    primary_key: str | None = None
    tolerance: dict[str, float] = Field(default_factory=dict)


class AuthorScenario(StrictModel):
    """Author-flow eval scenario."""

    id: str
    kind: Literal[EvalKind.AUTHOR] = EvalKind.AUTHOR
    template: str
    input_files: list[str]
    workflow_description: str
    expected_output_files: list[GoldenOutputSpec]
    expected_validation_layers: list[str]
    expected_terminal_status: SessionStatus = SessionStatus.COMPLETED


class ExpectedDiagnosis(StrictModel):
    suspected_file: str
    suspected_lines_range: tuple[int, int]
    root_cause_substring_any_of: list[str]


class ExpectedPatch(StrictModel):
    file: str
    hunks_count: int
    must_not_modify_files: list[str] = Field(default_factory=list)


class ExpectedAfterFix(StrictModel):
    pytest_passed: int
    pytest_failed: int = 0
    golden_diff_on_column: str | None = None
    golden_diff_count: int = 0


class RepairScenario(StrictModel):
    """Repair-flow eval scenario."""

    id: str
    kind: Literal[EvalKind.REPAIR] = EvalKind.REPAIR
    fixture: str
    problem_report_path: str
    expected_reproduction_method: Literal["pytest", "sample_run"] = "pytest"
    expected_failing_test: str | None = None
    expected_diagnosis: ExpectedDiagnosis
    expected_patch: ExpectedPatch
    expected_after_fix: ExpectedAfterFix
    expected_terminal_status: SessionStatus = SessionStatus.COMPLETED


class AdversarialSubkind(StrEnum):
    AUTHOR_WITH_INJECTION_IN_CSV = "author_with_injection_in_csv"


class AdversarialScenario(StrictModel):
    """Adversarial scenario (currently one: CSV-content injection in author flow)."""

    id: str
    kind: Literal[EvalKind.ADVERSARIAL] = EvalKind.ADVERSARIAL
    subkind: AdversarialSubkind
    template: str
    injection_payload: str
    injection_location: str
    workflow_description: str
    expected_behaviour: list[str]
    expected_terminal_status: SessionStatus = SessionStatus.COMPLETED


EvalScenario = Annotated[
    AuthorScenario | RepairScenario | AdversarialScenario,
    Field(discriminator="kind"),
]


class EvalRunResult(StrictModel):
    """Per-scenario run outcome."""

    scenario_id: str
    passed: bool
    latency_ms: int
    cost_usd: float = 0.0
    failure_reason: str | None = None
    diff_path: str | None = None


class EvalRunSummary(StrictModel):
    """Aggregate eval-run outcome."""

    id: UUID = Field(default_factory=uuid4)
    started_at: datetime
    completed_at: datetime | None = None
    total: int
    passed: int
    failed: int
    per_tag: dict[str, dict[str, int]] = Field(default_factory=dict)
    """{ 'author': {'passed': N, 'failed': N}, ... }"""
    results: list[EvalRunResult] = Field(default_factory=list)
