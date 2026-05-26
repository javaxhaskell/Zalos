"""Validation engine for the author flow (ADR-0007, six layers).

Public surface for the rest of the backend. The validate_output tool
(``agentforge.tools.validation_tools``) is the LLM-facing wrapper.
"""

from __future__ import annotations

from agentforge.validation.golden import (
    CellMismatch,
    GoldenDiffError,
    GoldenDiffResult,
    golden_diff,
    resolve_golden_primary_key,
)
from agentforge.validation.layers import (
    layer_bank_categoriser_semantics,
    layer_business_rules,
    layer_generated_pytest,
    layer_golden_output,
    layer_required_columns,
    layer_row_level,
    layer_schema,
    layer_template_reference_parity,
)
from agentforge.validation.repair import (
    assemble_files_changed,
    golden_diff_zero_from_validation_checks,
    render_repair_markdown,
)
from agentforge.validation.reporter import render_json, render_markdown, tier_for_check
from agentforge.validation.validation_architecture import (
    ValidationTier,
    classify_validation_check,
    validation_failure_error_code,
)

__all__ = [
    "CellMismatch",
    "GoldenDiffError",
    "GoldenDiffResult",
    "assemble_files_changed",
    "golden_diff",
    "golden_diff_zero_from_validation_checks",
    "resolve_golden_primary_key",
    "layer_bank_categoriser_semantics",
    "layer_business_rules",
    "layer_generated_pytest",
    "layer_golden_output",
    "layer_required_columns",
    "layer_row_level",
    "layer_schema",
    "layer_template_reference_parity",
    "render_json",
    "render_markdown",
    "render_repair_markdown",
    "tier_for_check",
    "ValidationTier",
    "classify_validation_check",
    "validation_failure_error_code",
]
