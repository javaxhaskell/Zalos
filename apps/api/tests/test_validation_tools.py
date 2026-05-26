"""Unit tests for validate_output, generate_validation_report, finalise_session (BP5c).

The three closing tools of the author flow. validate_output composes
the six layer functions into a typed report; generate_validation_report
writes it to disk; finalise_session marks the session COMPLETED.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from agentforge.persistence.workspace import WorkspaceError
from agentforge.schemas import (
    EventKind,
    SessionStatus,
    ValidationCheck,
    ValidationLayer,
    ValidationReport,
)
from agentforge.tools.validation_tools import (
    FinaliseSessionInput,
    GenerateValidationReportInput,
    ValidateOutputInput,
    finalise_session_handler,
    generate_validation_report_handler,
    validate_output_handler,
)


def _ws(ctx: Any) -> Path:
    return ctx.workspace_manager.get(ctx.session_id)


def _write_csv(path: Path, rows: list[dict[str, str]], header: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------


async def test_validate_output_returns_passing_report_on_aligned_csvs(
    tool_ctx: Any,
) -> None:
    workspace = _ws(tool_ctx)
    rows = [
        {"id": "1", "category": "Income"},
        {"id": "2", "category": "Refund"},
    ]
    (workspace / "outputs").mkdir(exist_ok=True)
    _write_csv(workspace / "outputs" / "out.csv", rows, ["id", "category"])
    _write_csv(workspace / "outputs" / "golden.csv", rows, ["id", "category"])
    _write_csv(workspace / "uploads" / "sample.csv", [{"id": "1"}, {"id": "2"}], ["id"])

    report = await validate_output_handler(
        ValidateOutputInput(
            output_path="outputs/out.csv",
            primary_key="id",
            expected_columns=["id", "category"],
            required_columns=["id", "category"],
            input_path="uploads/sample.csv",
            golden_path="outputs/golden.csv",
            allowed_enums={"category": ["Income", "Refund"]},
        ),
        tool_ctx,
    )
    assert report.overall_passed is True
    layers = {c.layer for c in report.checks}
    # The original six contract layers always run; auxiliary layers
    # (template_reference, semantic_rules) are emitted by the
    # orchestrator path, not the validate_output tool.
    assert layers == {
        ValidationLayer.SCHEMA,
        ValidationLayer.REQUIRED_COLUMNS,
        ValidationLayer.BUSINESS_RULES,
        ValidationLayer.ROW_LEVEL,
        ValidationLayer.GOLDEN_OUTPUT,
        ValidationLayer.GENERATED_PYTEST,
    }
    # Layer 6 (pytest) is SKIPPED when no generated_tests_path.
    pytest_check = next(c for c in report.checks if c.layer == ValidationLayer.GENERATED_PYTEST)
    assert pytest_check.skipped is True


async def test_validate_output_fails_when_columns_missing(tool_ctx: Any) -> None:
    workspace = _ws(tool_ctx)
    (workspace / "outputs").mkdir(exist_ok=True)
    _write_csv(workspace / "outputs" / "out.csv", [{"id": "1"}], ["id"])

    report = await validate_output_handler(
        ValidateOutputInput(
            output_path="outputs/out.csv",
            primary_key="id",
            expected_columns=["id", "category"],
        ),
        tool_ctx,
    )
    assert report.overall_passed is False
    schema_check = next(c for c in report.checks if c.layer == ValidationLayer.SCHEMA)
    assert schema_check.passed is False


async def test_validate_output_emits_validation_run_event(tool_ctx: Any) -> None:
    workspace = _ws(tool_ctx)
    (workspace / "outputs").mkdir(exist_ok=True)
    _write_csv(workspace / "outputs" / "out.csv", [{"id": "1"}], ["id"])

    await validate_output_handler(
        ValidateOutputInput(
            output_path="outputs/out.csv",
            primary_key="id",
            expected_columns=["id"],
        ),
        tool_ctx,
    )
    kinds = [e.kind for e in tool_ctx.event_log.read_all(tool_ctx.session_id)]
    assert EventKind.VALIDATION_RUN in kinds


# ---------------------------------------------------------------------------
# generate_validation_report
# ---------------------------------------------------------------------------


def _sample_report(session_id) -> ValidationReport:
    from datetime import UTC, datetime

    return ValidationReport(
        session_id=session_id,
        generated_at=datetime.now(UTC),
        overall_passed=True,
        checks=[
            ValidationCheck(
                layer=ValidationLayer.SCHEMA,
                name="Output schema",
                passed=True,
                evidence="all good",
            ),
        ],
    )


async def test_generate_validation_report_writes_markdown_and_json(
    tool_ctx: Any,
) -> None:
    report = _sample_report(tool_ctx.session_id)
    out = await generate_validation_report_handler(
        GenerateValidationReportInput(report=report), tool_ctx
    )
    assert out.markdown_path == "reports/system_validation_report.md"
    assert out.json_path == "reports/system_validation_report.json"
    workspace = _ws(tool_ctx)
    md = (workspace / out.markdown_path).read_text()
    assert "# Validation Report" in md
    assert "Overall: PASS" in md
    js = json.loads((workspace / out.json_path).read_text())
    assert js["overall_passed"] is True


async def test_generate_validation_report_emits_artifact_generated(
    tool_ctx: Any,
) -> None:
    report = _sample_report(tool_ctx.session_id)
    await generate_validation_report_handler(
        GenerateValidationReportInput(report=report), tool_ctx
    )
    kinds = [e.kind for e in tool_ctx.event_log.read_all(tool_ctx.session_id)]
    assert EventKind.ARTIFACT_GENERATED in kinds


# ---------------------------------------------------------------------------
# finalise_session
# ---------------------------------------------------------------------------


async def test_finalise_session_refuses_without_artifact_generated(
    tool_ctx: Any,
) -> None:
    with pytest.raises(WorkspaceError, match="ARTIFACT_GENERATED"):
        await finalise_session_handler(
            FinaliseSessionInput(), tool_ctx
        )


async def test_finalise_session_updates_manifest_to_completed(tool_ctx: Any) -> None:
    # Pre-emit an ARTIFACT_GENERATED so the guard passes.
    from agentforge.schemas import ActorType

    tool_ctx.event_log.append(
        session_id=tool_ctx.session_id,
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={"path": "reports/system_validation_report.md"},
        step=0,
    )

    out = await finalise_session_handler(
        FinaliseSessionInput(summary="all good"), tool_ctx
    )
    assert out.status == SessionStatus.COMPLETED
    manifest = tool_ctx.workspace_manager.read_manifest(tool_ctx.session_id)
    assert manifest.status == SessionStatus.COMPLETED
