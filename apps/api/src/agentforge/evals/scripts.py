"""Per-scenario ``FakeModelClient`` script builders for Repair evals.

Author evals use the configured model client at runtime. Fake Author
responses live only in tests so production eval code cannot manufacture
final Author artifacts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from agentforge.models import ModelResponse, TextBlock, ToolUseBlock
from agentforge.schemas import (
    FilesChangedEntry,
    ProblemClassification,
    RepairReport,
    ReproductionMethod,
    ReproductionResult,
    TestRunSummary,
    ValidationCheck,
    ValidationLayer,
    ValidationReport,
)

# ---------------------------------------------------------------------------
# Helpers — shared expected-report builders
# ---------------------------------------------------------------------------


def build_passing_validation_report(session_id: UUID) -> ValidationReport:
    """All-six-layers-pass shape the scripted model emits to
    ``generate_validation_report``. The orchestrator's
    ``validate_output`` observation is what actually decides PASS/FAIL
    — this is the report-rendering input only."""
    return ValidationReport(
        session_id=session_id,
        generated_at=datetime.now(UTC),
        overall_passed=True,
        checks=[
            ValidationCheck(
                layer=layer,
                name=layer.value.replace("_", " ").title(),
                passed=True,
                evidence="see validate_output observation",
            )
            for layer in ValidationLayer
        ],
    )


def build_repair_report(session_id: UUID, diagnosis_id: str) -> RepairReport:
    """Six-section RepairReport for the invoice_aging_v1 fix narrative."""
    return RepairReport(
        session_id=session_id,
        generated_at=datetime.now(UTC),
        problem=(
            "Some April-dated invoices are missing from the 0-30 aging "
            "bucket and others show up as PARSE_ERROR."
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
        validation_before=TestRunSummary(
            passed_count=2,
            failed_count=1,
            total_count=3,
            failing_tests=[
                "tests/test_aging.py::test_april_invoices_in_first_bucket"
            ],
        ),
        validation_after=TestRunSummary(
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


# Diff that fixes invoice_aging_v1's date-format bug. Lifted from
# test_repair_flow_e2e (same fixture, same bug, same fix).
_INVOICE_AGING_FIX_DIFF = (
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


# ---------------------------------------------------------------------------
# Repair scenario — invoice_aging_v1 (R-01)
# ---------------------------------------------------------------------------


def invoice_aging_script(
    *, session_id: UUID, diagnosis_id: str | None = None
) -> list[ModelResponse]:
    """14-turn repair script driving invoice_aging_v1 to 3-pass.

    INFO (9 turns): list_workspace → inspect_file → summarise →
    classify → run_pytest → record_reproduction → diagnose →
    propose_patch → text-end.
    FIX  (6 turns): apply_patch → run_pytest → run_python_script →
    validate_output → generate_repair_report → finalise_session.
    """
    diag_id = diagnosis_id or str(uuid4())
    report = build_repair_report(session_id, diag_id)
    return [
        # INFO -----------------------------------------------------
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
        ModelResponse(
            id="info_8",
            content=[
                ToolUseBlock(
                    id="tu_propose",
                    name="propose_patch",
                    input={
                        "diagnosis_id": diag_id,
                        "file": "working/agent.py",
                        "unified_diff": _INVOICE_AGING_FIX_DIFF,
                        "rationale": (
                            "Sample is MM-DD-YYYY (e.g., '03-21-2026'); "
                            "flipping the format string parses all rows correctly."
                        ),
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
        ModelResponse(
            id="info_end",
            content=[
                TextBlock(text="Diagnosis + patch ready. Proceeding to FIX.")
            ],
            stop_reason="end_turn",
        ),
        # FIX ------------------------------------------------------
        ModelResponse(
            id="fix_1",
            content=[
                ToolUseBlock(
                    id="tu_apply",
                    name="apply_patch",
                    input={
                        "file": "working/agent.py",
                        "unified_diff": _INVOICE_AGING_FIX_DIFF,
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
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
        ModelResponse(
            id="fix_6",
            content=[
                ToolUseBlock(
                    id="tu_finalise",
                    name="finalise_session",
                    input={
                        "summary": (
                            "Date-format bug fixed; all 3 tests pass; "
                            "output matches golden."
                        )
                    },
                ),
            ],
            stop_reason="tool_use",
        ),
    ]


__all__ = [
    "build_passing_validation_report",
    "build_repair_report",
    "invoice_aging_script",
]
