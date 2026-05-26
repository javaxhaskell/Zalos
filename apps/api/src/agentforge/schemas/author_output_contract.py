"""Author output contract — canonical deliverable spec before code generation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentforge.schemas.common import StrictModel

GoldenComparisonRequirement = Literal["required", "skipped", "supplied"]
AuthorBuildMode = Literal[
    "llm_custom",
    "clarification",
    "unsupported",
]
SheetRole = Literal[
    "primary_data",
    "reference_fx",
    "reference_policy",
    "lookup_table",
    "notes_metadata",
    "unknown",
]


class CalculatedFieldSpec(StrictModel):
    """One derived column the agent must populate on the row-level output."""

    name: str
    description: str
    formula: str | None = None


OutputColumnProducer = Literal[
    "copied_input",
    "classification",
    "rule_explanation",
    "calculation",
    "summary",
    "exception_flag",
    "other",
]


class OutputColumnSemanticsSpec(StrictModel):
    """Row-level semantics for one output column the generated agent must populate."""

    name: str
    description: str = ""
    producer_kind: OutputColumnProducer | str = "other"
    required: bool = True
    nullable: bool = False
    allow_empty_string: bool = False
    row_semantics: str
    fallback_value_semantics: str = ""


class AggregationSpec(StrictModel):
    """One grouped summary deliverable."""

    group_key: str
    output_path: str
    metrics: list[str] = Field(default_factory=list)


class ClassificationRuleSpec(StrictModel):
    """High-level business rule the generated agent should implement."""

    name: str
    description: str


class OutputFileSpec(StrictModel):
    """Structured description of one summary or exception output file."""

    path: str
    description: str = ""
    required_columns: list[str] = Field(default_factory=list)
    optional_columns: list[str] = Field(default_factory=list)


class ExceptionRuleSpec(StrictModel):
    """Structured exception rule the generated agent should implement."""

    name: str
    condition: str
    reason: str = ""
    severity: str = "medium"
    output_column: str | None = None


class ValidationCheckSpec(StrictModel):
    """Contract-level validation layer metadata."""

    check_id: str
    layer: str
    name: str
    required: bool = True
    check_type: str = "presence"
    output_file: str | None = None
    formula: str | None = None
    input_columns: list[str] = Field(default_factory=list)
    output_column: str | None = None
    tolerance: float | None = None
    severity: str | None = None


SummaryMetricType = Literal["sum", "count", "average", "min", "max", "custom"]


class SummaryMetricSpec(StrictModel):
    """Structured summary metric the generated agent should compute."""

    name: str
    metric_type: SummaryMetricType | str = "custom"
    source_column: str | None = None
    group_by: list[str] = Field(default_factory=list)
    filter: str | None = None
    description: str = ""
    formula: str | None = None
    input_columns: list[str] = Field(default_factory=list)
    output_column: str | None = None


DeliverableSourceLiteral = Literal[
    "user_explicit",
    "platform_canonical",
    "reviewed_contract_required",
]


class DeliverableSpec(StrictModel):
    """Structured requested or missing deliverable metadata."""

    name: str
    description: str = ""
    output_path: str | None = None
    required: bool = True
    status: str | None = None
    source: DeliverableSourceLiteral | None = None
    """Why this deliverable is required: user_explicit, platform_canonical, or
    reviewed_contract_required. Summary/exception CSV paths require user_explicit."""


def deliverable_label(entry: str | DeliverableSpec) -> str:
    """Return a path or name label for a string or structured deliverable spec."""
    if isinstance(entry, str):
        return entry
    return entry.output_path or entry.name


def deliverable_output_path(entry: str | DeliverableSpec) -> str | None:
    """Return the workspace-relative output path for a deliverable, if one exists."""
    if isinstance(entry, str):
        return entry if entry.startswith(("outputs/", "reports/")) else None
    if entry.output_path and entry.output_path.startswith(("outputs/", "reports/")):
        return entry.output_path
    return None


def deliverable_required(entry: str | DeliverableSpec) -> bool:
    """Return whether a deliverable entry should be treated as required.

    Plain path strings in requested_deliverables are informational hints only.
    Only structured DeliverableSpec entries with required=True are completion gates.
    Row-level output is always required via row_level_output_file.
    """
    if isinstance(entry, str):
        return False
    return entry.required


def output_file_path(entry: str | OutputFileSpec) -> str:
    """Return the workspace-relative path for a string or structured output spec."""
    if isinstance(entry, str):
        return entry
    return entry.path


class ClarificationQuestion(StrictModel):
    """Question to ask when the contract cannot be inferred safely."""

    question: str
    reason: str


class ColumnNullProfile(StrictModel):
    """Missingness profile for one column."""

    column: str
    empty_count: int = 0
    non_empty_count: int = 0


class ColumnUniquenessProfile(StrictModel):
    """Uniqueness profile for candidate key columns."""

    column: str
    unique_values: int = 0
    total_values: int = 0
    is_unique: bool = False


class TabularColumnProfile(StrictModel):
    """Inferred role of one input column."""

    name: str
    inferred_type: str = "string"
    sample_values: list[str] = Field(default_factory=list)


class WorkbookSheetProfile(StrictModel):
    """Inspection result for one XLSX worksheet."""

    sheet_name: str
    row_count: int
    columns: list[str] = Field(default_factory=list)
    inferred_role: SheetRole = "unknown"
    normalized_input_path: str | None = None
    sample_rows: list[dict[str, str]] = Field(default_factory=list)


class TabularFileProfile(StrictModel):
    """Rich inspection result for a CSV/XLSX upload."""

    original_upload_path: str
    input_format: str
    selected_sheet: str | None = None
    normalized_input_path: str
    agent_input_path: str
    columns: list[str]
    row_count: int
    column_profiles: list[TabularColumnProfile] = Field(default_factory=list)
    candidate_id_columns: list[str] = Field(default_factory=list)
    candidate_date_columns: list[str] = Field(default_factory=list)
    candidate_amount_columns: list[str] = Field(default_factory=list)
    candidate_fee_columns: list[str] = Field(default_factory=list)
    candidate_gross_columns: list[str] = Field(default_factory=list)
    candidate_net_columns: list[str] = Field(default_factory=list)
    candidate_debit_columns: list[str] = Field(default_factory=list)
    candidate_credit_columns: list[str] = Field(default_factory=list)
    candidate_status_columns: list[str] = Field(default_factory=list)
    candidate_category_columns: list[str] = Field(default_factory=list)
    candidate_grouping_columns: list[str] = Field(default_factory=list)
    candidate_reference_columns: list[str] = Field(default_factory=list)
    null_profiles: list[ColumnNullProfile] = Field(default_factory=list)
    uniqueness_profiles: list[ColumnUniquenessProfile] = Field(default_factory=list)
    sample_rows: list[dict[str, str]] = Field(default_factory=list)
    plausible_sheets: list[str] = Field(default_factory=list)
    workbook_sheets: list[WorkbookSheetProfile] = Field(default_factory=list)
    primary_sheet: str | None = None
    reference_sheets: list[str] = Field(default_factory=list)


class AuthorOutputContract(StrictModel):
    """Deliverable and validation contract produced before code generation."""

    workflow_type: str
    workflow_confidence: float = 0.0
    build_mode: AuthorBuildMode = "unsupported"
    input_file: str
    input_format: str
    selected_sheet: str | None = None
    normalized_input_path: str | None = None
    primary_sheet: str | None = None
    reference_sheets: list[str] = Field(default_factory=list)
    as_of_date: str | None = None
    requires_model_planning: bool = False
    primary_row_key: str | None = None
    row_level_output_file: str = "outputs/output.csv"
    summary_output_files: list[str | OutputFileSpec] = Field(default_factory=list)
    exception_output_files: list[str | OutputFileSpec] = Field(default_factory=list)
    input_columns: list[str] = Field(default_factory=list)
    output_columns: list[str] = Field(default_factory=list)
    required_output_columns: list[str] = Field(default_factory=list)
    optional_output_columns: list[str] = Field(default_factory=list)
    output_column_semantics: list[OutputColumnSemanticsSpec] = Field(default_factory=list)
    calculated_fields: list[CalculatedFieldSpec] = Field(default_factory=list)
    formula_input_columns: list[str] = Field(default_factory=list)
    formula_output_columns: list[str] = Field(default_factory=list)
    tolerances: dict[str, float] = Field(default_factory=dict)
    aggregation_specs: list[AggregationSpec] = Field(default_factory=list)
    summary_group_keys: list[str] = Field(default_factory=list)
    summary_metrics: list[str | SummaryMetricSpec] = Field(default_factory=list)
    exception_rules: list[str | ExceptionRuleSpec] = Field(default_factory=list)
    classification_rules: list[ClassificationRuleSpec] = Field(default_factory=list)
    validation_checks: list[ValidationCheckSpec] = Field(default_factory=list)
    golden_comparison_requirement: GoldenComparisonRequirement = "skipped"
    golden_output_path: str | None = None
    skipped_checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list)
    unsupported_reasons: list[str] = Field(default_factory=list)
    requested_deliverables: list[str | DeliverableSpec] = Field(default_factory=list)
    produced_deliverables: list[str] = Field(default_factory=list)
    missing_deliverables: list[str | DeliverableSpec] = Field(default_factory=list)
    preserve_row_count: bool = True
    allowed_enums: dict[str, list[str]] = Field(default_factory=dict)

    def all_declared_output_paths(self) -> list[str]:
        paths = [self.row_level_output_file]
        paths.extend(output_file_path(entry) for entry in self.summary_output_files)
        paths.extend(output_file_path(entry) for entry in self.exception_output_files)
        deduped: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if not path or path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    def requested_required_output_paths(self) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for entry in self.requested_deliverables:
            if not deliverable_required(entry):
                continue
            path = deliverable_output_path(entry)
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def output_column_semantics_by_name(self) -> dict[str, OutputColumnSemanticsSpec]:
        specs: dict[str, OutputColumnSemanticsSpec] = {}
        for spec in self.output_column_semantics:
            specs[spec.name] = spec
        return specs

    def required_output_column_semantics(self) -> list[OutputColumnSemanticsSpec]:
        by_name = self.output_column_semantics_by_name()
        return [
            by_name[column]
            for column in self.required_output_columns
            if column in by_name
        ]

    def all_required_output_paths(self) -> list[str]:
        requested = self.requested_required_output_paths()
        declared = self.all_declared_output_paths()
        if not requested:
            return declared
        paths = [self.row_level_output_file]
        paths.extend(path for path in requested if path != self.row_level_output_file)
        deduped: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if not path or path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    def is_ready_to_build(self) -> bool:
        return not self.clarification_questions and not self.unsupported_reasons

    def finalize_deliverables(self, *, produced_paths: list[str]) -> AuthorOutputContract:
        required = self.all_required_output_paths()
        missing = [path for path in required if path not in produced_paths]
        produced = [path for path in required if path in produced_paths]
        return self.model_copy(
            update={
                "produced_deliverables": produced,
                "missing_deliverables": missing,
            }
        )
