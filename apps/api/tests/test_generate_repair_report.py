"""Unit tests for ``generate_repair_report`` (Build Prompt 6).

The tool persists a typed RepairReport to ``reports/repair_report.{md,json}``
and emits both REPAIR_REPORT_GENERATED (repair-specific) and
ARTIFACT_GENERATED (the generic event that satisfies finalise_session's
state-machine guard).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentforge.schemas import (
    ArtifactType,
    EventKind,
    FilesChangedEntry,
    RepairReport,
)
from agentforge.schemas import TestRunSummary as RunSummary
from agentforge.tools.validation_tools import (
    GenerateRepairReportInput,
    generate_repair_report_handler,
)


def _ws(ctx: Any) -> Path:
    return ctx.workspace_manager.get(ctx.session_id)


def _sample_repair_report(session_id) -> RepairReport:
    return RepairReport(
        session_id=session_id,
        generated_at=datetime.now(UTC),
        problem="x",
        reproduction="x",
        diagnosis="x",
        files_changed=[
            FilesChangedEntry(
                file="working/agent.py",
                hunks_count=1,
                diff_hash="b" * 64,
                summary="fix",
            )
        ],
        validation_before=RunSummary(passed_count=2, failed_count=1, total_count=3),
        validation_after=RunSummary(passed_count=3, failed_count=0, total_count=3),
        golden_diff_zero=True,
    )


async def test_generate_repair_report_writes_md_and_json(tool_ctx: Any) -> None:
    report = _sample_repair_report(tool_ctx.session_id)
    out = await generate_repair_report_handler(
        GenerateRepairReportInput(report=report), tool_ctx
    )
    assert out.markdown_path == "reports/repair_report.md"
    assert out.json_path == "reports/repair_report.json"
    workspace = _ws(tool_ctx)
    md = (workspace / out.markdown_path).read_text()
    assert "# Repair Report" in md
    assert "## 1. Problem statement" in md
    js = json.loads((workspace / out.json_path).read_text())
    assert js["validation_after"]["passed_count"] == 3


async def test_generate_repair_report_emits_both_events(tool_ctx: Any) -> None:
    report = _sample_repair_report(tool_ctx.session_id)
    await generate_repair_report_handler(
        GenerateRepairReportInput(report=report), tool_ctx
    )
    events = tool_ctx.event_log.read_all(tool_ctx.session_id)
    kinds = [e.kind for e in events]
    assert EventKind.REPAIR_REPORT_GENERATED in kinds
    assert EventKind.ARTIFACT_GENERATED in kinds
    artifact = next(e for e in events if e.kind == EventKind.ARTIFACT_GENERATED)
    assert artifact.payload["artifact_type"] == ArtifactType.REPAIR_REPORT.value
    assert artifact.payload["path"] == "reports/repair_report.md"
