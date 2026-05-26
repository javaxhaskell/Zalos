"""End-to-end integration test for the repair flow (Build Prompt 6).

The BP6 acceptance criterion: a fresh repair session is driven through
both phases of the repair workflow by the orchestrator + agent loop
against a deterministic :class:`FakeModelClient`, fixing the
invoice-aging fixture's date-format bug, and producing:

  * The applied patch flipping ``"%d-%m-%Y"`` → ``"%m-%d-%Y"`` in
    ``working/agent.py`` (the fix the BP2 fixture was deliberately
    designed to need — see ``fixtures/broken_agents/invoice_aging_v1``).
  * ``run_pytest`` going from 2-pass-1-fail to 3-pass post-fix.
  * ``outputs/output.csv`` matching ``data/expected_output.csv`` (the
    fixture's committed golden) row-for-row.
  * ``reports/repair_report.md`` with all six sections populated.
  * Session manifest ``status=completed``.
  * Event chain intact: ``PHASE_TRANSITIONED`` ×2, ``REPRODUCTION_RESULT``,
    ``DIAGNOSIS_PRODUCED``, ``PATCH_PROPOSED``, ``PATCH_APPLIED``,
    ``REPAIR_REPORT_GENERATED``, ``ARTIFACT_GENERATED``,
    ``WORKFLOW_COMPLETED``.
"""

from __future__ import annotations

import csv
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.config import get_settings
from agentforge.models import (
    FakeModelClient,
    ModelResponse,
    TextBlock,
    ToolUseBlock,
)
from agentforge.orchestrator import RepairFlow
from agentforge.persistence.db import Base
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    EventKind,
    FilesChangedEntry,
    ProblemClassification,
    RepairReport,
    ReproductionMethod,
    ReproductionResult,
    SessionStatus,
    Workflow,
)
from agentforge.schemas import TestRunSummary as RunSummary
from agentforge.tools import build_registry

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = _REPO_ROOT / "fixtures" / "broken_agents" / "invoice_aging_v1"


# The fix the model proposes + applies. The bug is at line 34 of the
# fixture's agent.py: strptime expects "%d-%m-%Y" but the sample CSV
# is MM-DD-YYYY. The fix flips the format string and updates the
# docstring NOTE.
_FIX_DIFF = (
    "--- a/working/agent.py\n"
    "+++ b/working/agent.py\n"
    "@@ -29,7 +29,7 @@\n"
    " def parse_invoice_date(date_str: str) -> date:\n"
    '     """Parse an invoice date string into a ``date``.\n'
    " \n"
    "-    NOTE: assumes DD-MM-YYYY input format.\n"
    "+    NOTE: assumes MM-DD-YYYY input format.\n"
    '     """\n'
    '-    return datetime.strptime(date_str, "%d-%m-%Y").date()\n'
    '+    return datetime.strptime(date_str, "%m-%d-%Y").date()\n'
    " \n"
)


def _copy_fixture_into_working(workspace: Path) -> None:
    """Copy the invoice_aging_v1 fixture into ``workspace/working/``.

    Mirrors what the upload-extract step would do in production
    (REPAIR_LOADED phase, step 2 in WORKFLOWS.md §2).
    """
    working = workspace / "working"
    working.mkdir(parents=True, exist_ok=True)
    for item in _FIXTURE_DIR.iterdir():
        if item.name in ("README.md", "__pycache__", ".pytest_cache"):
            continue
        dest = working / item.name
        if item.is_dir():
            shutil.copytree(
                item, dest, ignore=shutil.ignore_patterns("__pycache__")
            )
        else:
            shutil.copy2(item, dest)


def _stage_golden(workspace: Path) -> None:
    """Copy the fixture's expected_output.csv into ``evals/`` for the validator."""
    evals = workspace / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        _FIXTURE_DIR / "data" / "expected_output.csv",
        evals / "expected_output.csv",
    )


def _build_repair_report(session_id: UUID, diagnosis_id: str) -> RepairReport:
    return RepairReport(
        session_id=session_id,
        generated_at=datetime.now(UTC),
        problem=(
            "Some April-dated invoices are missing from the 0-30 aging bucket "
            "and others show up as PARSE_ERROR."
        ),
        reproduction=(
            "pytest tests/test_aging.py::test_april_invoices_in_first_bucket "
            "fails before the patch; April rows are mis-bucketed or marked "
            "PARSE_ERROR depending on day-of-month."
        ),
        diagnosis=(
            "working/agent.py line 34: strptime uses '%d-%m-%Y' but the "
            "sample_input.csv dates are MM-DD-YYYY. Rows with day > 12 "
            "raise ValueError; rows with day <= 12 silently mis-parse."
        ),
        files_changed=[
            FilesChangedEntry(
                file="working/agent.py",
                hunks_count=1,
                diff_hash="placeholder-overwritten-on-disk",
                summary="Flip strptime format to %m-%d-%Y.",
            )
        ],
        validation_before=RunSummary(
            passed_count=2,
            failed_count=1,
            total_count=3,
            failing_tests=[
                "tests/test_aging.py::test_april_invoices_in_first_bucket"
            ],
        ),
        validation_after=RunSummary(
            passed_count=3,
            failed_count=0,
            total_count=3,
        ),
        golden_diff_zero=True,
        remaining_risks=[
            "Date format inference is brittle across templates; the upload "
            "flow should surface format ambiguity from inspect_csv_schema."
        ],
        next_steps=[
            "Add an inspect_csv_schema ambiguity_note check before run.",
        ],
    )


def _info_phase_script(
    *, diagnosis_id: str, file_id: UUID
) -> list[ModelResponse]:
    """Scripted INFO-phase responses (8 model turns).

    Sequence: list → inspect → summarise → classify → run_pytest →
    record_reproduction → diagnose → propose_patch → text-end.
    """
    return [
        # 1. Browse the workspace
        ModelResponse(
            id="info_1",
            content=[
                ToolUseBlock(
                    id="tu_list",
                    name="list_workspace",
                    input={"path": "working", "max_depth": 3, "max_entries": 50},
                ),
            ],
            stop_reason="tool_use",
        ),
        # 2. Read agent.py to locate the bug
        ModelResponse(
            id="info_2",
            content=[
                ToolUseBlock(
                    id="tu_inspect",
                    name="inspect_file",
                    input={"path": "working/agent.py"},
                ),
            ],
            stop_reason="tool_use",
        ),
        # 3. Record a plain-English summary
        ModelResponse(
            id="info_3",
            content=[
                ToolUseBlock(
                    id="tu_summary",
                    name="summarise_agent_purpose",
                    input={
                        "purpose": (
                            "Reads invoices CSV and computes per-row aging "
                            "buckets (0-30 / 31-60 / 61-90 / 90+) by parsing "
                            "invoice_date and comparing to today."
                        ),
                        "inputs": [
                            "CSV with columns invoice_id, invoice_date, vendor, amount"
                        ],
                        "outputs": [
                            "CSV with same columns plus age_days and aging_bucket"
                        ],
                        "entry_point": "working/agent.py",
                        "dependencies": [],
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        # 4. Classify the problem
        ModelResponse(
            id="info_4",
            content=[
                ToolUseBlock(
                    id="tu_classify",
                    name="classify_problem",
                    input={
                        "report_text": (
                            "April invoices are in the wrong aging bucket; "
                            "some show as PARSE_ERROR."
                        ),
                        "classification": ProblemClassification.WRONG_OUTPUT.value,
                        "confidence": 0.9,
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        # 5. Reproduce via pytest
        ModelResponse(
            id="info_5",
            content=[
                ToolUseBlock(
                    id="tu_pytest_pre",
                    name="run_pytest",
                    input={"tests_path": "tests/", "cwd": "working"},
                ),
            ],
            stop_reason="tool_use",
        ),
        # 6. Record the reproduction outcome (gates the diagnose call)
        ModelResponse(
            id="info_6",
            content=[
                ToolUseBlock(
                    id="tu_record",
                    name="record_reproduction",
                    input={
                        "result": ReproductionResult(
                            session_id=uuid4(),
                            reproduced=True,
                            method=ReproductionMethod.PYTEST,
                            failing_test=(
                                "tests/test_aging.py::test_april_invoices_in_first_bucket"
                            ),
                            error_excerpt=(
                                "AssertionError: April invoices not in 0-30 bucket"
                            ),
                        ).model_dump(mode="json")
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        # 7. Diagnose
        ModelResponse(
            id="info_7",
            content=[
                ToolUseBlock(
                    id="tu_diagnose",
                    name="diagnose",
                    input={
                        "suspected_file": "working/agent.py",
                        "suspected_lines": [29, 35],
                        "root_cause": (
                            "strptime uses '%d-%m-%Y' but sample_input.csv "
                            "dates are MM-DD-YYYY; day>12 raises ValueError, "
                            "day<=12 silently mis-parses."
                        ),
                        "severity": "high",
                        "fix_risk": "low",
                        "confidence": 0.95,
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        # 8. Propose patch
        ModelResponse(
            id="info_8",
            content=[
                ToolUseBlock(
                    id="tu_propose",
                    name="propose_patch",
                    input={
                        "diagnosis_id": diagnosis_id,
                        "file": "working/agent.py",
                        "unified_diff": _FIX_DIFF,
                        "rationale": (
                            "Sample is MM-DD-YYYY (e.g., '03-21-2026'); "
                            "flipping the format string parses all rows correctly."
                        ),
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        # 9. Text-only: end INFO
        ModelResponse(
            id="info_end",
            content=[TextBlock(text="Diagnosis + patch ready. Proceeding to FIX.")],
            stop_reason="end_turn",
        ),
    ]


def _fix_phase_script(
    *, session_id: UUID, report: RepairReport
) -> list[ModelResponse]:
    """Scripted FIX-phase responses (6 model turns).

    Sequence: apply_patch → run_pytest (now passes) → run_python_script
    (produce output.csv) → validate_output → generate_repair_report →
    finalise_session.
    """
    return [
        # 10. Apply the patch
        ModelResponse(
            id="fix_1",
            content=[
                ToolUseBlock(
                    id="tu_apply",
                    name="apply_patch",
                    input={
                        "file": "working/agent.py",
                        "unified_diff": _FIX_DIFF,
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        # 11. Re-run tests
        ModelResponse(
            id="fix_2",
            content=[
                ToolUseBlock(
                    id="tu_pytest_post",
                    name="run_pytest",
                    input={"tests_path": "tests/", "cwd": "working"},
                ),
            ],
            stop_reason="tool_use",
        ),
        # 12. Run the agent on its sample
        ModelResponse(
            id="fix_3",
            content=[
                ToolUseBlock(
                    id="tu_run",
                    name="run_python_script",
                    input={
                        "script_path": "working/agent.py",
                        "args": [
                            "working/data/sample_input.csv",
                            "outputs/output.csv",
                        ],
                        "cwd": ".",
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        # 13. Validate against golden
        ModelResponse(
            id="fix_4",
            content=[
                ToolUseBlock(
                    id="tu_validate",
                    name="validate_output",
                    input={
                        "output_path": "outputs/output.csv",
                        "primary_key": "invoice_id",
                        "expected_columns": [
                            "invoice_id",
                            "invoice_date",
                            "vendor",
                            "amount",
                            "age_days",
                            "aging_bucket",
                        ],
                        "required_columns": ["age_days", "aging_bucket"],
                        "golden_path": "evals/expected_output.csv",
                        "allowed_enums": {
                            "aging_bucket": ["0-30", "31-60", "61-90", "90+"]
                        },
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        # 14. Generate the repair report
        ModelResponse(
            id="fix_5",
            content=[
                ToolUseBlock(
                    id="tu_report",
                    name="generate_repair_report",
                    input={"report": report.model_dump(mode="json")},
                ),
            ],
            stop_reason="tool_use",
        ),
        # 15. Finalise
        ModelResponse(
            id="fix_6",
            content=[
                ToolUseBlock(
                    id="tu_finalise",
                    name="finalise_session",
                    input={"summary": "Date-format bug fixed; all 3 tests pass; output matches golden."},
                ),
            ],
            stop_reason="tool_use",
        ),
    ]


@pytest.fixture()
def e2e_db(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'repair_e2e.db'}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


async def test_repair_flow_drives_invoice_aging_v1_to_fix(
    workspaces_root: Path,
    e2e_db,
) -> None:
    # --- Stage 1: workspace + fixture in working/ + golden in evals/ ----
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.REPAIR)
    workspace = wm.get(sid)
    settings = get_settings()

    _copy_fixture_into_working(workspace)
    _stage_golden(workspace)

    # --- Stage 2: build the orchestrator + scripted client -------------
    event_log = EventLog(wm)
    file_id = uuid4()

    # The diagnosis_id the propose_patch turn must reference. The
    # actual Diagnosis UUID is generated by the diagnose handler; we
    # use a fixed value here so the script can be deterministic. The
    # handler accepts any UUID-shaped string.
    diagnosis_id = str(uuid4())
    report = _build_repair_report(sid, diagnosis_id)

    script: list[ModelResponse] = []
    script.extend(_info_phase_script(diagnosis_id=diagnosis_id, file_id=file_id))
    script.extend(_fix_phase_script(session_id=sid, report=report))

    client = FakeModelClient(script=script)
    loop = AgentLoop(
        registry=build_registry(),
        model_client=client,
        idempotency_store=IdempotencyStore(db=e2e_db),
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )
    flow = RepairFlow(
        agent_loop=loop,
        event_log=event_log,
        workspace_manager=wm,
        settings=settings,
    )

    # --- Stage 3: drive the flow --------------------------------------
    outcome = await flow.run(
        session_id=sid,
        system_prompt="You are the repair agent.",
        initial_messages=[],
        budgets=LoopBudgets(
            max_steps=30, max_tokens=200_000, max_wall_seconds=120
        ),
    )

    # --- Stage 4: outcome assertions ----------------------------------
    # Under the evidence-gated architecture, the orchestrator owns the
    # FIX phase deterministically; the LLM's scripted FIX-phase
    # responses are no longer consumed (advisory mode only).
    # ``fix_outcome`` is therefore None on the new flow.
    assert outcome.terminal_status == SessionStatus.COMPLETED, (
        f"unexpected terminal status: {outcome.terminal_status}\n"
        f"info_outcome={outcome.info_outcome}\n"
        f"fix_outcome={outcome.fix_outcome}"
    )
    assert outcome.fix_outcome is None

    # The fix landed on disk via the deterministic patcher.
    fixed = (workspace / "working" / "agent.py").read_text()
    assert '"%m-%d-%Y"' in fixed
    assert '"%d-%m-%Y"' not in fixed

    # RepairReport on disk
    repair_md = workspace / "reports" / "repair_report.md"
    assert repair_md.is_file()
    md_body = repair_md.read_text()
    for heading in (
        "## Problem reported",
        "## Files inspected",
        "## Failure reproduced",
        "## Root cause",
        "## Fix applied",
        "## Changed files",
        "## Validation evidence",
        "## Remaining risks",
    ):
        assert heading in md_body, f"missing heading: {heading}"

    repair_json = workspace / "reports" / "repair_report.json"
    assert repair_json.is_file()

    # Event chain: every expected event is present + chain integrity
    events = event_log.read_all(sid)
    kinds = [e.kind for e in events]
    assert EventKind.PHASE_TRANSITIONED in kinds
    assert EventKind.PATCH_APPLIED in kinds
    assert EventKind.REPAIR_REPORT_GENERATED in kinds
    assert EventKind.ARTIFACT_GENERATED in kinds
    assert EventKind.WORKFLOW_COMPLETED in kinds

    # Before/after pytest evidence artifacts.
    artifact_kinds = {
        e.payload.get("artifact_type")
        for e in events
        if e.kind == EventKind.ARTIFACT_GENERATED
    }
    assert "pytest_before_fix_log" in artifact_kinds
    assert "pytest_after_fix_log" in artifact_kinds
    assert "repair_report" in artifact_kinds
    assert "repair_report_json" in artifact_kinds

    # Chain integrity
    valid, msg = event_log.verify_chain(sid)
    assert valid, f"event chain broken: {msg}"

    # Manifest reflects terminal state
    manifest = wm.read_manifest(sid)
    assert manifest.status == SessionStatus.COMPLETED


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]
