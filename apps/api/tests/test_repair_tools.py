"""Unit tests for the repair-flow tools (Build Prompt 6).

Each tool is a typed-output enforcer: it takes a Pydantic-validated
hypothesis from the model, emits the corresponding canonical event,
and returns the typed object. Tests cover the happy path + the
constraints (confidence bounds, uuid validation, event emission).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from agentforge.schemas import (
    EventKind,
    ProblemClassification,
    ReproductionMethod,
    ReproductionResult,
)
from agentforge.tools.repair_tools import (
    ClassifyProblemInput,
    DiagnoseInput,
    ProposePatchInput,
    RecordReproductionInput,
    SummariseAgentPurposeInput,
    classify_problem_handler,
    diagnose_handler,
    propose_patch_handler,
    record_reproduction_handler,
    summarise_agent_purpose_handler,
)

# ---------------------------------------------------------------------------
# summarise_agent_purpose
# ---------------------------------------------------------------------------


async def test_summarise_agent_purpose_emits_event(tool_ctx: Any) -> None:
    out = await summarise_agent_purpose_handler(
        SummariseAgentPurposeInput(
            purpose="Computes invoice aging buckets",
            inputs=["CSV of invoices with invoice_date column"],
            outputs=["CSV with age_days + aging_bucket columns"],
            entry_point="working/agent.py",
            dependencies=["pandas"],
        ),
        tool_ctx,
    )
    assert out.entry_point == "working/agent.py"
    kinds = [e.kind for e in tool_ctx.event_log.read_all(tool_ctx.session_id)]
    assert EventKind.AGENT_SUMMARY_PRODUCED in kinds


# ---------------------------------------------------------------------------
# classify_problem
# ---------------------------------------------------------------------------


async def test_classify_problem_persists_typed_classification(tool_ctx: Any) -> None:
    out = await classify_problem_handler(
        ClassifyProblemInput(
            report_text="The April invoices end up in the wrong bucket.",
            classification=ProblemClassification.WRONG_OUTPUT,
            confidence=0.85,
        ),
        tool_ctx,
    )
    assert out.classification == ProblemClassification.WRONG_OUTPUT
    kinds = [e.kind for e in tool_ctx.event_log.read_all(tool_ctx.session_id)]
    assert EventKind.REPAIR_PROBLEM_RECEIVED in kinds


async def test_classify_problem_rejects_confidence_out_of_range(tool_ctx: Any) -> None:
    # Pydantic's Field(ge=0, le=1) on RepairProblem rejects this before
    # the handler sees it.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await classify_problem_handler(
            ClassifyProblemInput(
                report_text="x",
                classification=ProblemClassification.WRONG_OUTPUT,
                confidence=1.7,
            ),
            tool_ctx,
        )


# ---------------------------------------------------------------------------
# record_reproduction
# ---------------------------------------------------------------------------


async def test_record_reproduction_emits_reproduction_result(tool_ctx: Any) -> None:
    incoming = ReproductionResult(
        session_id=uuid4(),  # will be overwritten by the handler
        reproduced=True,
        method=ReproductionMethod.PYTEST,
        failing_test="tests/test_aging.py::test_april_invoices_in_first_bucket",
        error_excerpt="AssertionError: April invoices not in 0-30 bucket",
    )
    out = await record_reproduction_handler(
        RecordReproductionInput(result=incoming), tool_ctx
    )
    assert out.session_id == tool_ctx.session_id
    assert out.reproduced is True
    kinds = [e.kind for e in tool_ctx.event_log.read_all(tool_ctx.session_id)]
    assert EventKind.REPRODUCTION_RESULT in kinds


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


async def test_diagnose_emits_typed_diagnosis(tool_ctx: Any) -> None:
    out = await diagnose_handler(
        DiagnoseInput(
            suspected_file="working/agent.py",
            suspected_lines=(29, 35),
            root_cause="strptime format '%d-%m-%Y' but input is MM-DD-YYYY",
            severity="high",
            fix_risk="low",
            confidence=0.95,
        ),
        tool_ctx,
    )
    assert out.suspected_file == "working/agent.py"
    assert out.suspected_lines == (29, 35)
    kinds = [e.kind for e in tool_ctx.event_log.read_all(tool_ctx.session_id)]
    assert EventKind.DIAGNOSIS_PRODUCED in kinds


async def test_diagnose_rejects_confidence_out_of_range(tool_ctx: Any) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await diagnose_handler(
            DiagnoseInput(
                suspected_file="x.py",
                suspected_lines=(1, 2),
                root_cause="x",
                severity="low",
                fix_risk="low",
                confidence=1.1,
            ),
            tool_ctx,
        )


# ---------------------------------------------------------------------------
# propose_patch
# ---------------------------------------------------------------------------


async def test_propose_patch_emits_typed_proposal(tool_ctx: Any) -> None:
    diagnosis_id = str(uuid4())
    diff = (
        "--- a/working/agent.py\n"
        "+++ b/working/agent.py\n"
        "@@\n"
        "-    return datetime.strptime(date_str, \"%d-%m-%Y\").date()\n"
        "+    return datetime.strptime(date_str, \"%m-%d-%Y\").date()\n"
    )
    out = await propose_patch_handler(
        ProposePatchInput(
            diagnosis_id=diagnosis_id,
            file="working/agent.py",
            unified_diff=diff,
            rationale="Sample input is MM-DD-YYYY; flip strptime format.",
        ),
        tool_ctx,
    )
    assert isinstance(out.id, UUID)
    assert str(out.diagnosis_id) == diagnosis_id
    kinds = [e.kind for e in tool_ctx.event_log.read_all(tool_ctx.session_id)]
    assert EventKind.PATCH_PROPOSED in kinds


async def test_propose_patch_rejects_non_uuid_diagnosis_id(tool_ctx: Any) -> None:
    with pytest.raises(ValueError, match="UUID"):
        await propose_patch_handler(
            ProposePatchInput(
                diagnosis_id="not-a-uuid",
                file="x.py",
                unified_diff="--- a/x\n+++ b/x\n",
                rationale="x",
            ),
            tool_ctx,
        )
