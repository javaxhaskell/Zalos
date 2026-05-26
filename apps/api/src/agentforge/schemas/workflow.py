"""Workflow-specific schemas for author and repair flows."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from agentforge.schemas.common import Severity, StrictModel

# ---------------------------------------------------------------------------
# Author flow
# ---------------------------------------------------------------------------


class ColumnProfile(StrictModel):
    """Inferred shape of one column in an uploaded file."""

    name: str
    dtype: str
    """e.g. 'string', 'int64', 'float64', 'date', 'bool'."""
    null_rate: float
    sample_values: list[str] = Field(default_factory=list)
    ambiguity_note: str | None = None


class FileProfile(StrictModel):
    """Inferred schema for an uploaded file."""

    file_id: UUID
    filename: str
    row_count: int
    encoding: str = "utf-8"
    columns: list[ColumnProfile]
    sheet_name: str | None = None


class BusinessRule(StrictModel):
    """A typed rule the agent must respect in generated code.

    Rules are deliberately small: a name + plain-English description.
    The agent implements the rule in code; the validation engine asserts
    against the canonical form.
    """

    name: str
    description: str
    severity: Severity = Severity.MEDIUM


class AuthorRequirements(StrictModel):
    """Confirmed spec used to drive code generation."""

    session_id: UUID
    description: str
    input_file_ids: list[UUID]
    expected_output_columns: list[ColumnProfile]
    business_rules: list[BusinessRule]
    edge_cases: list[str] = Field(default_factory=list)
    validation_checks: list[str] = Field(default_factory=list)
    template_used: str | None = None


# ---------------------------------------------------------------------------
# Repair flow
# ---------------------------------------------------------------------------


class ProblemClassification(StrEnum):
    TEST_FAILURE = "test_failure"
    RUNTIME_ERROR = "runtime_error"
    WRONG_OUTPUT = "wrong_output"
    PERFORMANCE = "performance"
    OTHER = "other"


class RepairProblem(StrictModel):
    """User's reported problem with an existing agent."""

    session_id: UUID
    report_text: str
    classification: ProblemClassification
    confidence: float = Field(ge=0.0, le=1.0)


class AgentSummary(StrictModel):
    """Plain-English description of an existing agent."""

    session_id: UUID
    purpose: str
    inputs: list[str]
    outputs: list[str]
    entry_point: str
    dependencies: list[str] = Field(default_factory=list)


class ReproductionMethod(StrEnum):
    PYTEST = "pytest"
    SAMPLE_RUN = "sample_run"


class ReproductionResult(StrictModel):
    """Did the reported bug reproduce?"""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    reproduced: bool
    method: ReproductionMethod
    failing_test: str | None = None
    error_excerpt: str | None = None
    observation_id: UUID | None = None
    """ID of the underlying ToolObservation for cross-reference."""


class Diagnosis(StrictModel):
    """Inferred root cause for a reproducible bug."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    suspected_file: str
    suspected_lines: tuple[int, int]
    root_cause: str
    severity: Severity
    fix_risk: Severity
    confidence: float = Field(ge=0.0, le=1.0)


class PatchProposal(StrictModel):
    """A proposed fix."""

    id: UUID = Field(default_factory=uuid4)
    diagnosis_id: UUID
    file: str
    unified_diff: str
    rationale: str
    """Plain-English explanation surfaced above the diff in the ApprovalPanel."""


# ---------------------------------------------------------------------------
# Q&A
# ---------------------------------------------------------------------------


class QuestionStyle(StrEnum):
    FREE_TEXT = "free_text"
    RADIO = "radio"
    SELECT_COLUMN = "select_column"


class PendingQuestion(StrictModel):
    """A clarifying question that paused the session."""

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    text: str
    style: QuestionStyle = QuestionStyle.FREE_TEXT
    options: list[str] = Field(default_factory=list)


class QuestionAnswer(StrictModel):
    """User's response to a PendingQuestion."""

    question_id: UUID
    answer: str
