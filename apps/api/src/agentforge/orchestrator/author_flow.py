"""Author workflow orchestrator.

Uploaded CSV/XLSX authoring is routed to the LLM-first pipeline: the backend
profiles the file, the model authors and reviews the contract, the model
authors code and tests, and the backend executes and validates the results.
Legacy loop execution remains bounded and gated, but cannot complete unless
the model-authored provenance gate passes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from agentforge.agent import AgentLoop, LoopBudgets, LoopOutcome
from agentforge.config import Settings
from agentforge.models import ModelMessage
from agentforge.orchestrator import state_machine
from agentforge.orchestrator.author_custom_build import (
    CUSTOM_WORKFLOW_BUILD_VIA,
    execute_custom_workflow_pipeline,
)
from agentforge.orchestrator.author_intent import (
    AuthorPrePipelineGate,
    IntentSchemaAssessment,
    UploadFormat,
    assess_author_pre_pipeline,
    read_upload_header,
)
from agentforge.orchestrator.author_llm_authoring import (
    AI_AUTHORED_WORKFLOW_BUILD_VIA,
    enforce_author_completion_gate,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    ApprovalStatus,
    AuthorPhase,
    ErrorCode,
    EventKind,
    SessionStatus,
    ToolPhase,
)
from agentforge.schemas.event import WorkspaceEvent

_logger = logging.getLogger("agentforge.orchestrator.author_flow")

# Tools that get covering run-consent approvals before the BUILD phase.
# Per ADR-0006: the user grants run-consent once; the orchestrator emits
# a covering APPROVAL_GRANTED that the loop's gate honours for the
# remainder of the session.
_RUN_CONSENT_TOOLS: tuple[str, ...] = (
    "run_python_script",
    "run_pytest",
)

# Tools whose first invocation also benefits from a covering grant in
# BP5c. write_file/apply_patch should ideally have per-batch grants
# (the "Review Diff" gate per ADR-0006), but for BP5c the simplest
# correct behaviour is one covering grant per session — BP5d's
# orchestrator refinement narrows this.
_BUILD_COVERING_TOOLS: tuple[str, ...] = _RUN_CONSENT_TOOLS + (
    "write_file",
    "apply_patch",
    "generate_validation_report",
    "finalise_session",
)


@dataclass(frozen=True)
class UploadedDataFile:
    path: Path
    format: UploadFormat


@dataclass
class AuthorOutcome:
    """Composite outcome across both phases of the author flow."""

    session_id: UUID
    terminal_status: SessionStatus
    info_outcome: LoopOutcome
    build_outcome: LoopOutcome | None
    """``None`` if the INFO phase paused or errored before BUILD began."""
    terminal_error_code: ErrorCode | None = None
    phases_completed: list[AuthorPhase] = field(default_factory=list)


class AuthorFlow:
    """Drive the author workflow's two coarse phases."""

    def __init__(
        self,
        *,
        agent_loop: AgentLoop,
        event_log: EventLog,
        workspace_manager: WorkspaceManager,
        settings: Settings,
    ) -> None:
        self.agent_loop = agent_loop
        self.event_log = event_log
        self.workspace_manager = workspace_manager
        self.settings = settings

    async def run(
        self,
        *,
        session_id: UUID,
        system_prompt: str,
        initial_messages: list[ModelMessage],
        budgets: LoopBudgets,
    ) -> AuthorOutcome:
        # ------------------------------------------------------------------
        # Phase 1: INFO
        # ------------------------------------------------------------------
        state_machine.transition(
            session_id=session_id,
            from_phase=None,
            to_phase=AuthorPhase.AUTHOR_TEMPLATE,
            event_log=self.event_log,
        )

        early_outcome = await self._try_pre_author_validation_gate(
            session_id=session_id,
            template_name=None,
        )
        if early_outcome is not None:
            return early_outcome

        info_outcome = await self.agent_loop.run(
            session_id=session_id,
            phase=ToolPhase.AUTHOR_INFO,
            system_prompt=system_prompt,
            initial_messages=initial_messages,
            budgets=budgets,
            advisory_mode=False,
        )

        if info_outcome.paused:
            return AuthorOutcome(
                session_id=session_id,
                terminal_status=_status_from_outcome(info_outcome),
                info_outcome=info_outcome,
                build_outcome=None,
                terminal_error_code=info_outcome.terminal_error_code,
                phases_completed=[AuthorPhase.AUTHOR_TEMPLATE],
            )

        custom_outcome = await self._try_pre_author_validation_gate(
            session_id=session_id,
            template_name=None,
            step=info_outcome.steps_taken,
            info_outcome=info_outcome,
            stage_label="pre_build_custom_workflow_gate",
        )
        if custom_outcome is not None:
            return custom_outcome

        if info_outcome.terminal_error_code is not None:
            return AuthorOutcome(
                session_id=session_id,
                terminal_status=_status_from_outcome(info_outcome),
                info_outcome=info_outcome,
                build_outcome=None,
                terminal_error_code=info_outcome.terminal_error_code,
                phases_completed=[AuthorPhase.AUTHOR_TEMPLATE],
            )

        # ------------------------------------------------------------------
        # Transition INFO → BUILD
        # ------------------------------------------------------------------
        state_machine.transition(
            session_id=session_id,
            from_phase=AuthorPhase.AUTHOR_TEMPLATE,
            to_phase=AuthorPhase.AUTHOR_GENERATE,
            event_log=self.event_log,
            step=info_outcome.steps_taken,
        )

        # Pre-record covering APPROVAL_GRANTED events for the BUILD-phase
        # tools the model will need. In production these are user clicks
        # on the "Review diff" + "Run consent" + "Finalise" gates; the
        # orchestrator collapses them into single covering grants per
        # ADR-0006 so the loop's gate doesn't fire on every step.
        self._record_covering_grants(
            session_id=session_id,
            step=info_outcome.steps_taken,
            tools=_BUILD_COVERING_TOOLS,
        )

        # ------------------------------------------------------------------
        # Phase 2: BUILD
        # ------------------------------------------------------------------
        build_outcome = await self.agent_loop.run(
            session_id=session_id,
            phase=ToolPhase.AUTHOR_BUILD,
            system_prompt=system_prompt,
            initial_messages=info_outcome.messages,
            budgets=budgets,
            start_step=info_outcome.steps_taken,
        )

        terminal_status = _status_from_outcome(build_outcome)
        terminal_error = build_outcome.terminal_error_code
        via = "llm_build_phase"

        if terminal_status == SessionStatus.COMPLETED:
            gate_error = enforce_author_completion_gate(
                session_id=session_id,
                event_log=self.event_log,
                step=info_outcome.steps_taken + build_outcome.steps_taken,
                stage_label=AI_AUTHORED_WORKFLOW_BUILD_VIA,
                workspace=self.workspace_manager.get(session_id),
            )
            if gate_error is not None:
                terminal_status = SessionStatus.FAILED_OTHER
                terminal_error = gate_error
            else:
                via = AI_AUTHORED_WORKFLOW_BUILD_VIA

        if terminal_status == SessionStatus.COMPLETED:
            cumulative = self._cumulative_budget(session_id)
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_COMPLETED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "workflow": "author",
                    "steps_taken": cumulative["steps_used"],
                    "tokens_used": cumulative["tokens_used"],
                    "tool_calls_used": cumulative["tool_calls_used"],
                    "via": via,
                    "recovery_used": False,
                },
                step=info_outcome.steps_taken + build_outcome.steps_taken,
            )

        return AuthorOutcome(
            session_id=session_id,
            terminal_status=terminal_status,
            info_outcome=info_outcome,
            build_outcome=build_outcome,
            terminal_error_code=terminal_error,
            phases_completed=[
                AuthorPhase.AUTHOR_TEMPLATE,
                AuthorPhase.AUTHOR_GENERATE,
            ],
        )

    def _cumulative_budget(self, session_id: UUID) -> dict[str, int]:
        """Sum tokens / tool_calls / steps from the full event log.

        Used in ``workflow_completed`` payloads so the value the UI
        reads (cumulative across pause + resume) matches what the
        runner persists to the DB row + manifest. The single source
        of truth is the event log.
        """
        tokens = 0
        tool_calls = 0
        max_step = -1
        for evt in self.event_log.read_all(session_id):
            if evt.kind == EventKind.MODEL_CALLED:
                usage = evt.payload.get("usage") or {}
                tokens += int(usage.get("input_tokens") or 0)
                tokens += int(usage.get("output_tokens") or 0)
            if evt.kind == EventKind.TOOL_INVOKED:
                tool_calls += 1
            if isinstance(evt.step, int) and evt.step > max_step:
                max_step = evt.step
        return {
            "tokens_used": tokens,
            "tool_calls_used": tool_calls,
            "steps_used": max_step + 1 if max_step >= 0 else 0,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _author_workflow_description_from_events(
        self, events: list[WorkspaceEvent]
    ) -> str:
        for evt in reversed(events):
            if evt.kind != EventKind.DECISION_INPUT:
                continue
            if evt.payload.get("kind") != "author_user_workflow":
                continue
            text = evt.payload.get("text")
            if isinstance(text, str):
                return text.strip()
        return ""

    async def _try_pre_author_validation_gate(
        self,
        *,
        session_id: UUID,
        template_name: str | None,
        step: int = 0,
        info_outcome: LoopOutcome | None = None,
        stage_label: str = "pre_author_validation_gate",
    ) -> AuthorOutcome | None:
        """Route uploaded tabular Author sessions into the LLM-first build."""
        workspace = self.workspace_manager.get(session_id)
        events = self.event_log.read_all(session_id)
        description = self._author_workflow_description_from_events(events)
        if not description:
            return None
        upload = self._latest_uploaded_data_file(events=events, workspace=workspace)
        if upload is None:
            return None

        columns, _ = read_upload_header(upload.path)
        gate_kind, assessment = assess_author_pre_pipeline(
            user_description=description,
            template_name=template_name,
            columns=columns,
            upload_format=upload.format,
            upload_filename=upload.path.name,
        )
        if gate_kind == "proceed":
            return None
        if gate_kind == "custom_build":
            return await self._run_custom_workflow_pipeline(
                session_id=session_id,
                upload=upload,
                step=step,
                info_outcome=info_outcome,
                stage_label=stage_label,
            )

        if assessment is None:
            return None

        rel_path = str(upload.path.relative_to(workspace))
        terminal_status, terminal_error = self._apply_author_pre_pipeline_stop(
            session_id=session_id,
            template_name=template_name or "",
            upload_path=rel_path,
            upload_format=upload.format,
            gate_kind=gate_kind,
            assessment=assessment,
            step=step,
            stage_label=stage_label,
        )
        return AuthorOutcome(
            session_id=session_id,
            terminal_status=terminal_status,
            info_outcome=info_outcome or _empty_terminated_info_outcome(),
            build_outcome=None,
            terminal_error_code=terminal_error,
            phases_completed=[AuthorPhase.AUTHOR_TEMPLATE],
        )

    async def _run_custom_workflow_pipeline(
        self,
        *,
        session_id: UUID,
        upload: UploadedDataFile,
        step: int,
        info_outcome: LoopOutcome | None = None,
        stage_label: str = CUSTOM_WORKFLOW_BUILD_VIA,
    ) -> AuthorOutcome:
        state_machine.transition(
            session_id=session_id,
            from_phase=AuthorPhase.AUTHOR_TEMPLATE,
            to_phase=AuthorPhase.AUTHOR_GENERATE,
            event_log=self.event_log,
            step=step,
        )
        description = self._author_workflow_description_from_events(
            self.event_log.read_all(session_id)
        )
        terminal_status, terminal_error = await execute_custom_workflow_pipeline(
            session_id=session_id,
            workspace=self.workspace_manager.get(session_id),
            upload=upload,
            workflow_type=None,
            user_description=description,
            settings=self.settings,
            event_log=self.event_log,
            workspace_manager=self.workspace_manager,
            step=step,
            stage_label=stage_label,
            model_client=self.agent_loop.model_client,
        )
        if terminal_status == SessionStatus.COMPLETED:
            cumulative = self._cumulative_budget(session_id)
            manifest_path = self.workspace_manager.get(session_id) / "manifest.json"
            completion_via = CUSTOM_WORKFLOW_BUILD_VIA
            completed_workflow_type = None
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    completion = manifest.get("completion") or {}
                    completion_via = completion.get("completion_via") or completion_via
                    completed_workflow_type = (
                        completion.get("workflow_type") or completed_workflow_type
                    )
                except json.JSONDecodeError:
                    pass
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_COMPLETED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "workflow": "author",
                    "steps_taken": cumulative["steps_used"],
                    "tokens_used": cumulative["tokens_used"],
                    "tool_calls_used": cumulative["tool_calls_used"],
                    "via": completion_via,
                    "workflow_type": completed_workflow_type,
                    "recovery_used": False,
                },
                step=step,
            )
        return AuthorOutcome(
            session_id=session_id,
            terminal_status=terminal_status,
            info_outcome=info_outcome or _empty_terminated_info_outcome(),
            build_outcome=None,
            terminal_error_code=terminal_error,
            phases_completed=[
                AuthorPhase.AUTHOR_TEMPLATE,
                AuthorPhase.AUTHOR_GENERATE,
            ],
        )

    def _apply_author_pre_pipeline_stop(
        self,
        *,
        session_id: UUID,
        template_name: str,
        upload_path: str,
        upload_format: UploadFormat,
        gate_kind: AuthorPrePipelineGate,
        assessment: IntentSchemaAssessment,
        step: int,
        stage_label: str,
    ) -> tuple[SessionStatus, ErrorCode | None]:
        if gate_kind == "intent_mismatch":
            self._record_intent_schema_mismatch(
                session_id=session_id,
                template_name=template_name or "bank_categoriser",
                assessment=assessment,
                input_path=upload_path,
                step=step,
                stage_label=stage_label,
            )
            if assessment.needs_clarification:
                question_id = str(uuid4())
                self.event_log.append(
                    session_id=session_id,
                    kind=EventKind.QUESTION_ASKED,
                    actor_type=ActorType.SYSTEM,
                    payload={
                        "question_event_id": question_id,
                        "plain_english_question": assessment.clarification_question,
                        "technical_context": assessment.explanation,
                    },
                    step=step,
                )
                return (SessionStatus.PAUSED_USER, None)
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.AUTHOR_INTENT_SCHEMA_MISMATCH.value,
                    "message": assessment.explanation,
                    "stage": stage_label,
                    "detected_file_type": assessment.detected_file_type,
                    "requested_workflow": assessment.requested_workflow,
                    "missing_columns": assessment.missing_columns,
                    "next_steps": assessment.next_steps,
                },
                step=step,
            )
            return (
                SessionStatus.FAILED_OTHER,
                ErrorCode.AUTHOR_INTENT_SCHEMA_MISMATCH,
            )

        self._record_custom_workflow_not_validated(
            session_id=session_id,
            template_name=template_name or None,
            assessment=assessment,
            input_path=upload_path,
            upload_format=upload_format,
            step=step,
            stage_label=stage_label,
        )
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_FAILED,
            actor_type=ActorType.SYSTEM,
            payload={
                "error_code": ErrorCode.AUTHOR_CUSTOM_WORKFLOW_NOT_VALIDATED.value,
                "message": assessment.explanation,
                "stage": stage_label,
                "detected_file_type": assessment.detected_file_type,
                "requested_workflow": assessment.requested_workflow,
                "detected_columns": assessment.detected_columns,
                "next_steps": assessment.next_steps,
            },
            step=step,
        )
        return (
            SessionStatus.FAILED_OTHER,
            ErrorCode.AUTHOR_CUSTOM_WORKFLOW_NOT_VALIDATED,
        )

    def _record_intent_schema_mismatch(
        self,
        *,
        session_id: UUID,
        template_name: str,
        assessment: IntentSchemaAssessment,
        input_path: str,
        step: int,
        stage_label: str,
    ) -> None:
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "author_intent_schema_mismatch",
                "stage": stage_label,
                "template": template_name,
                "input_path": input_path,
                "detected_file_type": assessment.detected_file_type,
                "requested_workflow": assessment.requested_workflow,
                "missing_columns": assessment.missing_columns,
                "explanation": assessment.explanation,
                "next_steps": assessment.next_steps,
                "needs_clarification": assessment.needs_clarification,
            },
            step=step,
        )

    def _record_custom_workflow_not_validated(
        self,
        *,
        session_id: UUID,
        template_name: str | None,
        assessment: IntentSchemaAssessment,
        input_path: str,
        upload_format: UploadFormat,
        step: int,
        stage_label: str,
    ) -> None:
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "author_custom_workflow_not_validated",
                "stage": stage_label,
                "template": template_name,
                "input_path": input_path,
                "upload_format": upload_format,
                "detected_file_type": assessment.detected_file_type,
                "requested_workflow": assessment.requested_workflow,
                "detected_columns": assessment.detected_columns,
                "explanation": assessment.explanation,
                "next_steps": assessment.next_steps,
            },
            step=step,
        )

    def _latest_uploaded_data_file(
        self,
        *,
        events: list[WorkspaceEvent],
        workspace: Path,
    ) -> UploadedDataFile | None:
        for evt in reversed(events):
            if evt.kind != EventKind.FILE_UPLOADED:
                continue
            rel = evt.payload.get("relative_path")
            if not isinstance(rel, str):
                continue
            lowered = rel.lower()
            if not lowered.endswith((".csv", ".xlsx")):
                continue
            candidate = workspace / rel
            if not candidate.is_file():
                continue
            fmt: UploadFormat = "xlsx" if lowered.endswith(".xlsx") else "csv"
            return UploadedDataFile(path=candidate, format=fmt)

        uploads = workspace / "uploads"
        if uploads.is_dir():
            candidates = sorted(
                (
                    p
                    for p in uploads.iterdir()
                    if p.is_file() and p.suffix.lower() in {".csv", ".xlsx"}
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                path = candidates[0]
                fmt = "xlsx" if path.suffix.lower() == ".xlsx" else "csv"
                return UploadedDataFile(path=path, format=fmt)
        return None

    def _record_covering_grants(
        self,
        *,
        session_id: UUID,
        step: int,
        tools: tuple[str, ...],
    ) -> None:
        """Emit one ``APPROVAL_GRANTED`` per tool with ``scope='session'``.

        These satisfy :meth:`AgentLoop._has_approval`'s covering-grant
        branch so the model can dispatch the gated tools without further
        per-step approvals.
        """
        now = datetime.now(UTC)
        for tool_name in tools:
            request_id = str(uuid4())
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.APPROVAL_GRANTED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "request_id": request_id,
                    "tool_name": tool_name,
                    "scope": "session",
                    "status": ApprovalStatus.GRANTED.value,
                    "decided_by": "system-orchestrator",
                    "rationale": (
                        "covering grant for ADR-0006 batched approvals "
                        "(synthesised by AuthorFlow until per-gate user "
                        "consent lands in BP5d/BP7)"
                    ),
                },
                step=step,
                ts=now,
            )
            _logger.info(
                "covering_grant",
                extra={"tool_name": tool_name, "session_id": str(session_id)},
            )


def _empty_terminated_info_outcome() -> LoopOutcome:
    """Placeholder when the orchestrator stops before the INFO loop runs."""
    return LoopOutcome(
        terminated=True,
        paused=False,
        pause_reason=None,
        steps_taken=0,
        tokens_used=0,
        wall_seconds=0.0,
        messages=[],
        last_response=None,
    )


def _status_from_outcome(outcome: LoopOutcome) -> SessionStatus:
    """Map a :class:`LoopOutcome` to a terminal :class:`SessionStatus`."""
    if outcome.paused:
        return (
            SessionStatus.PAUSED_APPROVAL
            if outcome.pause_reason == "approval"
            else SessionStatus.PAUSED_USER
        )
    code = outcome.terminal_error_code
    if code is None:
        return SessionStatus.COMPLETED
    if code in (
        ErrorCode.BUDGET_EXHAUSTED_STEPS,
        ErrorCode.BUDGET_EXHAUSTED_TOKENS,
        ErrorCode.BUDGET_EXHAUSTED_WALL_TIME,
        ErrorCode.BUDGET_EXHAUSTED_TOOL_CALLS,
        ErrorCode.BUDGET_EXHAUSTED_FILE_COUNT,
    ):
        return SessionStatus.FAILED_BUDGET
    if code == ErrorCode.VALIDATION_LOOP_EXHAUSTED:
        return SessionStatus.FAILED_MODEL
    return SessionStatus.FAILED_OTHER
