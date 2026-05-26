"""Four-tier Author validation architecture classification.

The LLM authors the workflow-specific contract and workflow-specific tests.
The backend provides universal + contract-driven validators. Golden comparison
is optional independent evidence.
"""

from __future__ import annotations

from enum import StrEnum

from agentforge.schemas import ValidationCheck, ValidationLayer, ValidationReport
from agentforge.schemas.common import ErrorCode


class ValidationTier(StrEnum):
    """Architectural grouping for Author validation checks."""

    UNIVERSAL = "universal"
    CONTRACT_SPECIFIC = "contract_specific"
    GENERATED_PYTEST = "generated_pytest"
    GOLDEN_OUTPUT = "golden_output"
    AUXILIARY = "auxiliary"


TIER_SECTION_COPY: dict[ValidationTier, tuple[str, str]] = {
    ValidationTier.UNIVERSAL: (
        "Universal validation",
        "Checks that every generated workflow must satisfy, such as required "
        "artifacts, row preservation, report presence, safety, and archive readiness.",
    ),
    ValidationTier.CONTRACT_SPECIFIC: (
        "Contract-specific validation",
        "Checks derived from the model-authored AuthorOutputContract, such as "
        "required output columns, enum values, primary keys, exception consistency, "
        "summary files, and deliverables.",
    ),
    ValidationTier.GENERATED_PYTEST: (
        "Generated pytest",
        "Model-authored workflow-specific tests for this particular workflow. "
        "These may test sample-specific business behaviours derived from the "
        "prompt, file, and contract.",
    ),
    ValidationTier.GOLDEN_OUTPUT: (
        "Golden-output comparison",
        "Optional independent expected-output check. This runs only when a "
        "separate expected output file is supplied.",
    ),
    ValidationTier.AUXILIARY: (
        "Auxiliary reference checks",
        "Optional additional validation evidence, such as example-specific or "
        "golden-output-adjacent checks. These checks do not replace the normal "
        "Author path and do not steer code generation.",
    ),
}

_TIER_ORDER: tuple[ValidationTier, ...] = (
    ValidationTier.UNIVERSAL,
    ValidationTier.CONTRACT_SPECIFIC,
    ValidationTier.GENERATED_PYTEST,
    ValidationTier.GOLDEN_OUTPUT,
    ValidationTier.AUXILIARY,
)


def classify_validation_check(check: ValidationCheck) -> ValidationTier:
    """Map a :class:`ValidationCheck` to its architectural tier."""
    if check.layer == ValidationLayer.GENERATED_PYTEST:
        return ValidationTier.GENERATED_PYTEST
    if check.layer == ValidationLayer.GOLDEN_OUTPUT:
        return ValidationTier.GOLDEN_OUTPUT
    if check.layer in {
        ValidationLayer.TEMPLATE_REFERENCE,
        ValidationLayer.SEMANTIC_RULES,
    }:
        return ValidationTier.AUXILIARY
    if check.name == "Required deliverables present":
        return ValidationTier.UNIVERSAL
    if check.layer == ValidationLayer.ROW_LEVEL and check.name == "Row-level invariants":
        return ValidationTier.UNIVERSAL
    return ValidationTier.CONTRACT_SPECIFIC


def group_checks_by_tier(
    checks: list[ValidationCheck],
) -> dict[ValidationTier, list[ValidationCheck]]:
    grouped: dict[ValidationTier, list[ValidationCheck]] = {
        tier: [] for tier in _TIER_ORDER
    }
    for check in checks:
        grouped[classify_validation_check(check)].append(check)
    return grouped


def primary_failed_check(report: ValidationReport) -> ValidationCheck | None:
    for check in report.checks:
        if check.skipped or check.passed is True:
            continue
        return check
    return None


def validation_failure_error_code(
    *,
    report: ValidationReport | None = None,
    pytest_failed: bool = False,
    safety_failed: bool = False,
    artifact_failed: bool = False,
) -> ErrorCode:
    """Choose the most specific layer error code for a failed Author run."""
    if safety_failed:
        return ErrorCode.SAFETY_VALIDATION_FAILED
    if artifact_failed:
        return ErrorCode.ARTIFACT_VALIDATION_FAILED
    if pytest_failed:
        return ErrorCode.GENERATED_PYTEST_FAILED
    if report is None:
        return ErrorCode.AUTHOR_VALIDATION_FAILED
    failed = primary_failed_check(report)
    if failed is None:
        return ErrorCode.AUTHOR_VALIDATION_FAILED
    tier = classify_validation_check(failed)
    if tier == ValidationTier.GOLDEN_OUTPUT:
        return ErrorCode.GOLDEN_OUTPUT_COMPARISON_FAILED
    if tier == ValidationTier.CONTRACT_SPECIFIC:
        return ErrorCode.CONTRACT_SPECIFIC_VALIDATION_FAILED
    if tier == ValidationTier.UNIVERSAL:
        return ErrorCode.UNIVERSAL_VALIDATION_FAILED
    if tier == ValidationTier.GENERATED_PYTEST:
        return ErrorCode.GENERATED_PYTEST_FAILED
    return ErrorCode.AUTHOR_VALIDATION_FAILED


__all__ = [
    "TIER_SECTION_COPY",
    "ValidationTier",
    "classify_validation_check",
    "group_checks_by_tier",
    "primary_failed_check",
    "validation_failure_error_code",
]
