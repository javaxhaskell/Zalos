"""Render :class:`ValidationReport` to the on-disk markdown + JSON sidecar.

Format is locked in ``CONTRACTS.md`` §7. The markdown is what the
finance user sees in the report panel; the JSON is the machine-readable
sidecar that the eval runner (BP10) consumes.
"""

from __future__ import annotations

import json

from agentforge.schemas import ValidationCheck, ValidationReport
from agentforge.validation.validation_architecture import (
    TIER_SECTION_COPY,
    ValidationTier,
    classify_validation_check,
    group_checks_by_tier,
)


def render_markdown(report: ValidationReport) -> str:
    """Render the report as markdown matching the contract format."""
    lines: list[str] = []
    lines.append(f"# Validation Report — Session {report.session_id}")
    lines.append("")
    lines.append(f"Generated: {report.generated_at.isoformat()}")
    lines.append("")
    overall = "PASS" if report.overall_passed else "FAIL"
    lines.append(f"## Overall: {overall}")
    lines.append("")

    grouped = group_checks_by_tier(report.checks)
    check_idx = 0
    for tier in (
        ValidationTier.UNIVERSAL,
        ValidationTier.CONTRACT_SPECIFIC,
        ValidationTier.GENERATED_PYTEST,
        ValidationTier.GOLDEN_OUTPUT,
        ValidationTier.AUXILIARY,
    ):
        tier_checks = grouped[tier]
        if not tier_checks:
            continue
        title, description = TIER_SECTION_COPY[tier]
        lines.append(f"## {title}")
        lines.append("")
        lines.append(description)
        lines.append("")
        for check in tier_checks:
            check_idx += 1
            lines.extend(_render_check(check_idx, check, tier))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_json(report: ValidationReport) -> str:
    """Render the canonical JSON sidecar with ``validation_tier`` per check."""
    payload = report.model_dump(mode="json")
    payload["checks"] = [
        {
            **check.model_dump(mode="json"),
            "validation_tier": classify_validation_check(check).value,
        }
        for check in report.checks
    ]
    return json.dumps(payload, indent=2)


def _render_check(idx: int, check: ValidationCheck, tier: ValidationTier) -> list[str]:
    if check.skipped:
        status = "SKIPPED"
    elif check.passed is True:
        status = "PASS"
    else:
        status = "FAIL"
    tier_label = tier.value.replace("_", " ")
    out = [
        f"### Check {idx}: {check.name}",
        f"- Tier: {tier_label}",
        f"- Status: {status}",
        f"- Evidence: {check.evidence}",
    ]
    if check.detail:
        out.append("- Details:")
        for line in check.detail.splitlines():
            out.append(f"    {line}")
    if check.passed is False and not check.skipped and check.hint_if_failed:
        out.append(f"- Hint: {check.hint_if_failed}")
    return out


def tier_for_check(check: ValidationCheck) -> ValidationTier:
    """Public alias used by tests and orchestrator helpers."""
    return classify_validation_check(check)


__all__ = ["render_json", "render_markdown", "tier_for_check"]
