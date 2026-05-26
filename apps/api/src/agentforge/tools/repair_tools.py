"""Repair-flow tools (Build Prompt 6).

Five read-style tools that turn the LLM's analytical hypotheses into
typed, audited events. The pattern is *typed-output enforcement*: the
model proposes a typed analysis (an :class:`AgentSummary`, a
:class:`Diagnosis`, etc.), the tool validates it through Pydantic and
emits the corresponding canonical event so the audit chain records who
proposed what at which step. The "intelligence" is the LLM; the tools
exist to fence the proposal into a typed contract and write it to the
event log (INV-6).

Why a tool rather than letting the LLM emit free-form text the
orchestrator parses? Two reasons:

  * **Pydantic validation at the boundary.** A misshaped diagnosis
    cannot land on the audit log — the tool rejects it and the loop
    re-prompts (INV-8).
  * **State-machine prerequisites.** The repair state machine refuses
    to enter ``repair_diagnose`` without a recorded ``REPRODUCTION_RESULT``
    event (WORKFLOWS.md cross-cutting rules); ``record_reproduction``
    is the only way to emit that event, which makes the precondition
    enforceable end-to-end.
"""

from __future__ import annotations

from uuid import uuid4

from agentforge.schemas import (
    ActorType,
    AgentSummary,
    Diagnosis,
    EventKind,
    PatchProposal,
    ProblemClassification,
    RepairProblem,
    ReproductionResult,
    RiskLevel,
    StrictModel,
    ToolDefinition,
    ToolPhase,
)
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import RegisteredTool

_AUTHORIZE_CALLABLE = "agentforge.tools.authz.allow_authenticated_users"
_INFO_PHASE: list[ToolPhase] = [ToolPhase.REPAIR_INFO]


# ---------------------------------------------------------------------------
# summarise_agent_purpose
# ---------------------------------------------------------------------------


class SummariseAgentPurposeInput(StrictModel):
    """Arguments for ``summarise_agent_purpose``.

    The model has read the uploaded agent's files via ``inspect_file``
    and constructs the typed summary; this tool persists it.
    """

    purpose: str
    """One-paragraph plain-English description of what the agent does."""
    inputs: list[str]
    """Human-readable input descriptions (e.g., ``"CSV of bank transactions"``)."""
    outputs: list[str]
    entry_point: str
    """Path to the agent's CLI entrypoint, workspace-relative."""
    dependencies: list[str] = []


async def summarise_agent_purpose_handler(
    args: SummariseAgentPurposeInput, ctx: ToolContext
) -> AgentSummary:
    summary = AgentSummary(
        session_id=ctx.session_id,
        purpose=args.purpose,
        inputs=args.inputs,
        outputs=args.outputs,
        entry_point=args.entry_point,
        dependencies=args.dependencies,
    )
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.AGENT_SUMMARY_PRODUCED,
        actor_type=ActorType.MODEL,
        payload=summary,
        step=ctx.step,
    )
    return summary


SUMMARISE_AGENT_PURPOSE_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="summarise_agent_purpose",
        description=(
            "Record a plain-English summary of the uploaded agent's "
            "purpose, inputs, outputs, entry point, and dependencies. "
            "Call after inspecting the agent's files; the user reviews "
            "this summary in the next approval gate."
        ),
        input_schema_name="SummariseAgentPurposeInput",
        output_schema_name="AgentSummary",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_INFO_PHASE,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=SummariseAgentPurposeInput,
    output_schema=AgentSummary,
    handler=summarise_agent_purpose_handler,
)


# ---------------------------------------------------------------------------
# classify_problem
# ---------------------------------------------------------------------------


class ClassifyProblemInput(StrictModel):
    """Arguments for ``classify_problem``."""

    report_text: str
    """The user's verbatim problem report. Captured for the RepairReport."""
    classification: ProblemClassification
    """The model's typed classification."""
    confidence: float
    """Model's confidence in ``classification``, in ``[0.0, 1.0]``."""


async def classify_problem_handler(
    args: ClassifyProblemInput, ctx: ToolContext
) -> RepairProblem:
    # Confidence range is enforced by Pydantic on RepairProblem.confidence
    # (Field(ge=0.0, le=1.0)); rely on it rather than duplicating the check.
    problem = RepairProblem(
        session_id=ctx.session_id,
        report_text=args.report_text,
        classification=args.classification,
        confidence=args.confidence,
    )
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.REPAIR_PROBLEM_RECEIVED,
        actor_type=ActorType.MODEL,
        payload=problem,
        step=ctx.step,
    )
    return problem


CLASSIFY_PROBLEM_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="classify_problem",
        description=(
            "Classify the user's reported problem into TEST_FAILURE / "
            "RUNTIME_ERROR / WRONG_OUTPUT / PERFORMANCE / OTHER, with "
            "a confidence score. Emits REPAIR_PROBLEM_RECEIVED."
        ),
        input_schema_name="ClassifyProblemInput",
        output_schema_name="RepairProblem",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_INFO_PHASE,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=ClassifyProblemInput,
    output_schema=RepairProblem,
    handler=classify_problem_handler,
)


# ---------------------------------------------------------------------------
# record_reproduction
# ---------------------------------------------------------------------------


class RecordReproductionInput(StrictModel):
    """Arguments for ``record_reproduction``.

    The model has just run ``run_pytest`` (or ``run_python_script``) to
    attempt to reproduce the user's reported failure. This tool
    persists the typed result so the state-machine guard for
    ``repair_diagnose`` can fire (WORKFLOWS.md §cross-cutting rule 1).
    """

    result: ReproductionResult


async def record_reproduction_handler(
    args: RecordReproductionInput, ctx: ToolContext
) -> ReproductionResult:
    result = args.result.model_copy(update={"session_id": ctx.session_id})
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.REPRODUCTION_RESULT,
        actor_type=ActorType.MODEL,
        payload=result,
        step=ctx.step,
    )
    return result


RECORD_REPRODUCTION_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="record_reproduction",
        description=(
            "Record the outcome of a reproduction attempt (pytest or "
            "sample-run). Emits REPRODUCTION_RESULT — required by the "
            "state machine before diagnose can fire. Call after the "
            "first run_pytest in the repair info phase."
        ),
        input_schema_name="RecordReproductionInput",
        output_schema_name="ReproductionResult",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_INFO_PHASE,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=RecordReproductionInput,
    output_schema=ReproductionResult,
    handler=record_reproduction_handler,
)


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class DiagnoseInput(StrictModel):
    """Arguments for ``diagnose``.

    Constructed by the model after inspecting the relevant files
    (typically via ``inspect_file``) and reviewing the reproduction
    outcome. Pydantic validates the typed object before it lands on the
    event log.
    """

    suspected_file: str
    """Workspace-relative path to the file containing the bug."""
    suspected_lines: tuple[int, int]
    """``(start, end)`` line numbers, inclusive, in ``suspected_file``."""
    root_cause: str
    """One-paragraph plain-English explanation of the root cause."""
    severity: str
    """One of the :class:`Severity` enum values (info / low / medium / high / critical)."""
    fix_risk: str
    """How risky the proposed fix is, same enum as severity."""
    confidence: float = 0.0
    """Model's confidence in the diagnosis, in ``[0.0, 1.0]``."""


async def diagnose_handler(args: DiagnoseInput, ctx: ToolContext) -> Diagnosis:
    # Confidence range is enforced by Pydantic on Diagnosis.confidence.
    from agentforge.schemas.common import Severity

    diagnosis = Diagnosis(
        session_id=ctx.session_id,
        suspected_file=args.suspected_file,
        suspected_lines=args.suspected_lines,
        root_cause=args.root_cause,
        severity=Severity(args.severity),
        fix_risk=Severity(args.fix_risk),
        confidence=args.confidence,
    )
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.DIAGNOSIS_PRODUCED,
        actor_type=ActorType.MODEL,
        payload=diagnosis,
        step=ctx.step,
    )
    return diagnosis


DIAGNOSE_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="diagnose",
        description=(
            "Record a typed Diagnosis: which file + line range, the "
            "root cause in plain English, severity, fix risk, and "
            "confidence. Emits DIAGNOSIS_PRODUCED. The state machine "
            "refuses this if no REPRODUCTION_RESULT is on record yet."
        ),
        input_schema_name="DiagnoseInput",
        output_schema_name="Diagnosis",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_INFO_PHASE,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=DiagnoseInput,
    output_schema=Diagnosis,
    handler=diagnose_handler,
)


# ---------------------------------------------------------------------------
# propose_patch
# ---------------------------------------------------------------------------


class ProposePatchInput(StrictModel):
    """Arguments for ``propose_patch``.

    The actual edit lands later via ``apply_patch`` in the repair fix
    phase; this tool just records the proposal so the user can review
    it in the next approval gate and the audit log captures the
    rationale.
    """

    diagnosis_id: str
    """The ``id`` of the prior :class:`Diagnosis` (as a UUID string)."""
    file: str
    """Workspace-relative path the diff targets."""
    unified_diff: str
    """Standard unified-diff format; same shape ``apply_patch`` will receive."""
    rationale: str
    """Plain-English explanation surfaced above the diff in the ApprovalPanel."""


async def propose_patch_handler(
    args: ProposePatchInput, ctx: ToolContext
) -> PatchProposal:
    from uuid import UUID

    try:
        diagnosis_uuid = UUID(args.diagnosis_id)
    except ValueError as exc:
        raise ValueError(
            f"diagnosis_id must be a UUID string; got {args.diagnosis_id!r}"
        ) from exc

    proposal = PatchProposal(
        id=uuid4(),
        diagnosis_id=diagnosis_uuid,
        file=args.file,
        unified_diff=args.unified_diff,
        rationale=args.rationale,
    )
    ctx.event_log.append(
        session_id=ctx.session_id,
        kind=EventKind.PATCH_PROPOSED,
        actor_type=ActorType.MODEL,
        payload=proposal,
        step=ctx.step,
    )
    return proposal


PROPOSE_PATCH_TOOL = RegisteredTool(
    definition=ToolDefinition(
        name="propose_patch",
        description=(
            "Record a typed PatchProposal: file, unified diff, and "
            "plain-English rationale. References the prior diagnosis by "
            "id. Emits PATCH_PROPOSED. The actual file edit lands via "
            "apply_patch in the repair fix phase."
        ),
        input_schema_name="ProposePatchInput",
        output_schema_name="PatchProposal",
        risk_level=RiskLevel.READ,
        requires_approval=False,
        idempotent=True,
        phases=_INFO_PHASE,
        authorize_callable=_AUTHORIZE_CALLABLE,
    ),
    input_schema=ProposePatchInput,
    output_schema=PatchProposal,
    handler=propose_patch_handler,
)


__all__ = [
    "CLASSIFY_PROBLEM_TOOL",
    "ClassifyProblemInput",
    "DIAGNOSE_TOOL",
    "DiagnoseInput",
    "PROPOSE_PATCH_TOOL",
    "ProposePatchInput",
    "RECORD_REPRODUCTION_TOOL",
    "RecordReproductionInput",
    "SUMMARISE_AGENT_PURPOSE_TOOL",
    "SummariseAgentPurposeInput",
]
