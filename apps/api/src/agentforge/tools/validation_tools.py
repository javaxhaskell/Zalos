"""Validation + finalisation tools (Build Prompt 5c).

Three tools that close the author flow:

  * ``validate_output`` — read; runs the six ADR-0007 layers and
    returns a :class:`ValidationReport`. Internally dispatches pytest
    via :class:`SandboxRunner` for Layer 6 when a tests directory is
    provided.
  * ``generate_validation_report`` — low_write; renders the report to
    ``reports/system_validation_report.md`` + JSON sidecar. Auto-approved
    per ADR-0006 whitelist (source is the prior validate_output
    observation; destination is bounded to the workspace).
  * ``finalise_session`` — low_write; updates the session manifest to
    ``status=COMPLETED`` and emits ``WORKFLOW_COMPLETED``. The agent
    loop terminates on the success observation.

The model dispatches all three from the BUILD phase. Approval gating
for ``finalise_session`` is satisfied by the orchestrator's covering
grant (see :mod:`agentforge.orchestrator.author_flow`).
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentforge.persistence.workspace import WorkspaceError
from agentforge.sandbox import SandboxRunner
from agentforge.schemas import (
    ActorType,
    ArtifactType,
    BusinessRule,
    ErrorCode,
    EventKind,
    PerTestResult,
    RepairReport,
    RiskLevel,
    SessionStatus,
    StrictModel,
    TestResults,
    ToolDefinition,
    ToolPhase,
    ValidationReport,
)
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import RegisteredTool
from agentforge.validation import (
    layer_business_rules,
    layer_generated_pytest,
    layer_golden_output,
    layer_required_columns,
    layer_row_level,
    layer_schema,
    render_json,
    render_markdown,
    render_repair_markdown,
)

_AUTHORIZE_CALLABLE = "agentforge.tools.authz.allow_authenticated_users"

# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------


class ValidateOutputInput(StrictModel):
    """Arguments for ``validate_output``.

    Most fields are optional; layers without their inputs gracefully
    skip with ``passed=True`` and an evidence string noting the skip.
    The orchestrator (BP5c) stages the golden CSV and tests directory
    before this tool fires.
    """

    output_path: str
    """Workspace-relative path to the actual output CSV."""
    primary_key: str
    """Column used to row-align actual vs golden."""
    expected_columns: list[str]
    """Columns Layer 1 requires."""
    required_columns: list[str] = []
    """Columns Layer 2 requires non-null on every row."""
    input_path: str | None = None
    """Workspace-relative path to the input CSV (Layer 4 row-count check)."""
    golden_path: str | None = None
    """Workspace-relative path to the golden output CSV (Layer 5)."""
    generated_tests_path: str | None = None
    """Workspace-relative path to the generated tests/ directory (Layer 6)."""
    generated_tests_cwd: str = "."
    """Workspace-relative cwd for the pytest subprocess. Default workspace root."""
    allowed_enums: dict[str, list[str]] = {}
    """Layer 3 enum constraints (e.g., ``{'category': ['Income', 'Refund', ...]}``)."""
    business_rules: list[BusinessRule] = []
    """Layer 3 narrative. Names are reported in the report's detail."""


async def validate_output_handler(
    args: ValidateOutputInput, ctx: ToolContext
) -> ValidationReport:
    """Run all six layers; produce a typed :class:`ValidationReport`."""
    actual = ctx.workspace_manager.resolve_in(ctx.session_id, args.output_path)
    input_csv: Path | None = None
    if args.input_path is not None:
        input_csv = ctx.workspace_manager.resolve_in(
            ctx.session_id, args.input_path
        )
    golden_csv: Path | None = None
    if args.golden_path is not None:
        golden_csv = ctx.workspace_manager.resolve_in(
            ctx.session_id, args.golden_path
        )

    checks = [
        layer_schema(actual_csv=actual, expected_columns=args.expected_columns),
        layer_required_columns(
            actual_csv=actual, required_columns=args.required_columns
        ),
        layer_business_rules(
            actual_csv=actual,
            rules=args.business_rules,
            allowed_enums=args.allowed_enums,
        ),
        layer_row_level(
            actual_csv=actual,
            input_csv=input_csv,
            primary_key=args.primary_key,
        ),
        layer_golden_output(
            actual_csv=actual,
            golden_csv=golden_csv,
            primary_key=args.primary_key,
        ),
        layer_generated_pytest(
            test_results=_run_pytest_for_layer_6(args=args, ctx=ctx)
        ),
    ]
    # Skipped checks don't count for/against overall.
    overall = all(c.passed is True for c in checks if not c.skipped)
    report = ValidationReport(
        session_id=ctx.session_id,
        generated_at=datetime.now(UTC),
        overall_passed=overall,
        checks=checks,
    )

    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.VALIDATION_RUN,
        actor_type=ActorType.SYSTEM,
        payload={
            "overall_passed": overall,
            "layer_results": [
                {"layer": c.layer.value, "passed": c.passed} for c in checks
            ],
        },
        step=ctx.step,
    )
    return report


def _run_pytest_for_layer_6(
    *, args: ValidateOutputInput, ctx: ToolContext
) -> TestResults | None:
    if args.generated_tests_path is None:
        return None
    # Resolve to make sure the path is inside the workspace (raises if not).
    ctx.workspace_manager.resolve_in(ctx.session_id, args.generated_tests_path)
    runner = SandboxRunner(
        settings=ctx.settings, workspace_manager=ctx.workspace_manager
    )
    result = runner.run(
        session_id=ctx.session_id,
        cmd=[sys.executable, "-m", "pytest", "-v", args.generated_tests_path],
        cwd_relative=args.generated_tests_cwd,
        timeout_seconds=ctx.settings.subprocess_timeout_pytest,
        step=ctx.step,
    )
    return _parse_pytest_minimal(result.stdout, stderr=result.stderr)


# Minimal copy of the BP5b pytest parser, kept here to avoid cross-module
# coupling between validation and execution_tools. If a third caller
# appears, fold both into a shared parser utility.
_PYTEST_LINE_RE = re.compile(
    r"^(?P<path>\S+)::(?P<name>\S+)\s+(?P<status>PASSED|FAILED|SKIPPED|ERROR)"
    r"(?:\s+\[\s*\d+%\])?\s*$"
)
_PYTEST_SUMMARY_RE = re.compile(
    r"=+\s*"
    r"(?:(?P<failed>\d+)\s+failed,?\s*)?"
    r"(?:(?P<passed>\d+)\s+passed,?\s*)?"
    r"(?:(?P<skipped>\d+)\s+skipped,?\s*)?"
    r"(?:(?P<errors>\d+)\s+errors?,?\s*)?"
    r".*?in\s+[\d.]+s.*=+"
)


_PYTEST_COLLECTED_RE = re.compile(r"collected\s+(?P<count>\d+)\s+item")

GENERATED_PYTEST_MIN_COLLECTED = 3
"""Preferred minimum discoverable tests for Author generated-pytest gate."""


def _parse_pytest_minimal(stdout: str, *, stderr: str = "") -> TestResults:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    per_test: list[PerTestResult] = []
    summary_line = ""
    collected_count = 0
    for line in combined.splitlines():
        collected_match = _PYTEST_COLLECTED_RE.search(line)
        if collected_match:
            collected_count = max(collected_count, int(collected_match.group("count")))
        m = _PYTEST_LINE_RE.match(line.rstrip())
        if m:
            stripped = m.group("name").removeprefix("test_")
            per_test.append(
                PerTestResult(
                    name=f"{m.group('path')}::{m.group('name')}",
                    status=m.group("status").lower(),
                    humanised_name=stripped.replace("_", " ").capitalize(),
                    latency_ms=0,
                )
            )
            continue
        if _PYTEST_SUMMARY_RE.search(line):
            summary_line = line.strip()
    counts = {"failed": 0, "passed": 0, "skipped": 0, "errors": 0}
    if summary_line:
        m = _PYTEST_SUMMARY_RE.search(summary_line)
        if m:
            for key in counts:
                value = m.group(key)
                if value is not None:
                    counts[key] = int(value)
    elif per_test:
        for t in per_test:
            key = "errors" if t.status == "error" else t.status
            counts[key] += 1
        summary_line = (
            f"{counts['failed']} failed, {counts['passed']} passed "
            "(parsed from per-test lines)"
        )
    if collected_count == 0 and per_test:
        collected_count = len(per_test)
    total_count = max(sum(counts.values()), collected_count, len(per_test))
    if collected_count == 0 and total_count == 0 and "no tests collected" in combined.lower():
        summary_line = summary_line or "no tests collected"
    return TestResults(
        invocation_id=uuid4(),
        passed_count=counts["passed"],
        failed_count=counts["failed"],
        skipped_count=counts["skipped"],
        error_count=counts["errors"],
        total_count=total_count,
        collected_count=collected_count,
        summary_line=summary_line or "no tests collected",
        per_test=per_test,
        raw_output_excerpt=combined[-4096:] if len(combined) > 4096 else combined,
    )


def effective_collected_test_count(test_results: TestResults | None) -> int:
    if test_results is None:
        return 0
    if test_results.collected_count > 0:
        return test_results.collected_count
    if test_results.per_test:
        return len(test_results.per_test)
    return test_results.total_count


def generated_pytest_gate_failed(
    test_results: TestResults | None,
    exit_code: int | None,
    *,
    min_collected: int = GENERATED_PYTEST_MIN_COLLECTED,
) -> bool:
    """Return True when generated pytest did not satisfy collection/pass gates."""
    if exit_code == 5:
        return True
    if test_results is None:
        return exit_code not in (None, 0)
    collected = effective_collected_test_count(test_results)
    if collected < 1:
        return True
    if collected < min_collected:
        return True
    if test_results.failed_count > 0 or test_results.error_count > 0:
        return True
    if test_results.passed_count < 1:
        return True
    if "no tests collected" in test_results.summary_line.lower():
        return True
    if exit_code not in (None, 0):
        return True
    return False


VALIDATE_OUTPUT_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="validate_output",
        description=(
            "Run the six-layer validation engine against the agent's "
            "output CSV. Returns a typed ValidationReport with per-"
            "layer pass/fail + evidence. Layer 5 (golden) and Layer 6 "
            "(pytest) skip gracefully if their inputs aren't provided."
        ),
        input_schema_name="ValidateOutputInput",
        output_schema_name="ValidationReport",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=[ToolPhase.AUTHOR_BUILD],
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=ValidateOutputInput,
    output_schema=ValidationReport,
    handler=validate_output_handler,
)


# ---------------------------------------------------------------------------
# generate_validation_report
# ---------------------------------------------------------------------------


class GenerateValidationReportInput(StrictModel):
    """Arguments for ``generate_validation_report``.

    The model passes the prior ``validate_output`` observation's
    ValidationReport back in. The tool serialises it to disk.
    """

    report: ValidationReport


class GenerateValidationReportOutput(StrictModel):
    markdown_path: str
    json_path: str
    overall_passed: bool


async def generate_validation_report_handler(
    args: GenerateValidationReportInput, ctx: ToolContext
) -> GenerateValidationReportOutput:
    from agentforge.orchestrator.workflow_artifacts import (
        choose_system_validation_report_path,
        system_validation_report_json_path,
    )

    reports_dir = ctx.workspace_manager.resolve_in(ctx.session_id, "reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = ctx.workspace_manager.get(ctx.session_id)

    md_rel = choose_system_validation_report_path(set())
    md_path = workspace_root / md_rel
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_rel = system_validation_report_json_path(md_rel)
    json_path = workspace_root / json_rel
    md_body = render_markdown(args.report).encode("utf-8")
    json_body = render_json(args.report).encode("utf-8")
    md_path.write_bytes(md_body)
    json_path.write_bytes(json_body)

    md_digest = hashlib.sha256(md_body).hexdigest()
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={
            "artifact_type": ArtifactType.SYSTEM_VALIDATION_REPORT.value,
            "path": md_rel,
            "hash_sha256": md_digest,
            "size_bytes": len(md_body),
            "overall_passed": args.report.overall_passed,
        },
        step=ctx.step,
    )
    return GenerateValidationReportOutput(
        markdown_path=md_rel,
        json_path=json_rel,
        overall_passed=args.report.overall_passed,
    )


GENERATE_VALIDATION_REPORT_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="generate_validation_report",
        description=(
            "Write the ValidationReport (returned by validate_output) to "
            "reports/system_validation_report.md plus a JSON sidecar. Emits "
            "ARTIFACT_GENERATED. Auto-approved per ADR-0006."
        ),
        input_schema_name="GenerateValidationReportInput",
        output_schema_name="GenerateValidationReportOutput",
        risk_level=RiskLevel.LOW_WRITE,
        requires_approval=False,
        idempotent=True,
        phases=[ToolPhase.AUTHOR_BUILD],
        authorize_callable=_AUTHORIZE_CALLABLE,
        adr_override="ADR-0006",
    ),
    input_schema=GenerateValidationReportInput,
    output_schema=GenerateValidationReportOutput,
    handler=generate_validation_report_handler,
)


# ---------------------------------------------------------------------------
# generate_repair_report
# ---------------------------------------------------------------------------


class GenerateRepairReportInput(StrictModel):
    """Arguments for ``generate_repair_report``.

    The model assembles a :class:`RepairReport` from prior events
    (REPAIR_PROBLEM_RECEIVED for the verbatim user problem, the latest
    REPRODUCTION_RESULT + DIAGNOSIS_PRODUCED + PATCH_APPLIED, and
    before/after TestResults) and passes it here for persistence.
    """

    report: RepairReport


class GenerateRepairReportOutput(StrictModel):
    markdown_path: str
    json_path: str


async def generate_repair_report_handler(
    args: GenerateRepairReportInput, ctx: ToolContext
) -> GenerateRepairReportOutput:
    reports_dir = ctx.workspace_manager.resolve_in(ctx.session_id, "reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = ctx.workspace_manager.get(ctx.session_id)

    md_path = reports_dir / "repair_report.md"
    json_path = reports_dir / "repair_report.json"
    md_body = render_repair_markdown(args.report).encode("utf-8")
    json_body = args.report.model_dump_json(indent=2).encode("utf-8")
    md_path.write_bytes(md_body)
    json_path.write_bytes(json_body)

    md_rel = str(md_path.relative_to(workspace_root)).replace("\\", "/")
    json_rel = str(json_path.relative_to(workspace_root)).replace("\\", "/")
    md_digest = hashlib.sha256(md_body).hexdigest()

    # Repair-specific event so the audit chain shows the report was
    # generated, plus the generic ARTIFACT_GENERATED so the
    # finalise_session guard fires.
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.REPAIR_REPORT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={"path": md_rel, "hash_sha256": md_digest},
        step=ctx.step,
    )
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={
            "artifact_type": ArtifactType.REPAIR_REPORT.value,
            "path": md_rel,
            "hash_sha256": md_digest,
            "size_bytes": len(md_body),
        },
        step=ctx.step,
    )
    return GenerateRepairReportOutput(markdown_path=md_rel, json_path=json_rel)


GENERATE_REPAIR_REPORT_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="generate_repair_report",
        description=(
            "Write the typed RepairReport (six-section: problem, "
            "reproduction, diagnosis, files-changed, validation, "
            "remaining-risks) to reports/repair_report.md plus a JSON "
            "sidecar. Emits REPAIR_REPORT_GENERATED + ARTIFACT_GENERATED. "
            "Auto-approved per ADR-0006."
        ),
        input_schema_name="GenerateRepairReportInput",
        output_schema_name="GenerateRepairReportOutput",
        risk_level=RiskLevel.LOW_WRITE,
        requires_approval=False,
        idempotent=True,
        phases=[ToolPhase.REPAIR_FIX],
        authorize_callable=_AUTHORIZE_CALLABLE,
        adr_override="ADR-0006",
    ),
    input_schema=GenerateRepairReportInput,
    output_schema=GenerateRepairReportOutput,
    handler=generate_repair_report_handler,
)


# ---------------------------------------------------------------------------
# finalise_session
# ---------------------------------------------------------------------------


class FinaliseSessionInput(StrictModel):
    """Arguments for ``finalise_session``."""

    summary: str = ""
    """Optional one-line summary surfaced to the user. The orchestrator
    derives a default from the ValidationReport if absent."""


class FinaliseSessionOutput(StrictModel):
    session_id: str
    status: SessionStatus
    workspace_path: str
    completed_at: datetime


async def finalise_session_handler(
    args: FinaliseSessionInput, ctx: ToolContext
) -> FinaliseSessionOutput:
    # Refuse to finalise without at least one ARTIFACT_GENERATED on record
    # (mirrors the state-machine guard but in tool-handler space so the
    # observation explains why finalisation is being rejected).
    events = ctx.event_log.read_all(ctx.session_id)
    if not any(e.kind == EventKind.ARTIFACT_GENERATED for e in events):
        raise WorkspaceError(
            "cannot finalise: no ARTIFACT_GENERATED event recorded "
            "(the agent must produce a validation report or repair report first)"
        )

    workspace = ctx.workspace_manager.get(ctx.session_id)
    now = datetime.now(UTC)
    manifest = ctx.workspace_manager.update_manifest(
        ctx.session_id,
        status=SessionStatus.COMPLETED.value,
    )

    return FinaliseSessionOutput(
        session_id=str(ctx.session_id),
        status=SessionStatus.COMPLETED,
        workspace_path=str(workspace),
        completed_at=manifest.updated_at if manifest.updated_at else now,
    )


FINALISE_SESSION_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="finalise_session",
        description=(
            "Mark the session as completed. Refuses to finalise if no "
            "ARTIFACT_GENERATED event is on record (a validation or "
            "repair report must exist first). The agent loop terminates "
            "on successful dispatch of this tool."
        ),
        input_schema_name="FinaliseSessionInput",
        output_schema_name="FinaliseSessionOutput",
        risk_level=RiskLevel.LOW_WRITE,
        requires_approval=True,
        idempotent=True,
        phases=[ToolPhase.AUTHOR_BUILD, ToolPhase.REPAIR_FIX],
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=FinaliseSessionInput,
    output_schema=FinaliseSessionOutput,
    handler=finalise_session_handler,
)


# Module-level alias to satisfy "no unused imports" on ErrorCode for
# future error-typed paths (handler raises ErrorCode-typed exceptions
# in BP6+).
_ = ErrorCode


__all__ = [
    "FINALISE_SESSION_TOOL",
    "FinaliseSessionInput",
    "FinaliseSessionOutput",
    "GENERATE_REPAIR_REPORT_TOOL",
    "GENERATE_VALIDATION_REPORT_TOOL",
    "GenerateRepairReportInput",
    "GenerateRepairReportOutput",
    "GenerateValidationReportInput",
    "GenerateValidationReportOutput",
    "VALIDATE_OUTPUT_TOOL",
    "ValidateOutputInput",
]
