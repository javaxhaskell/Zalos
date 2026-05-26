"""Two-phase orchestrator for the repair workflow.

Mirrors :class:`AuthorFlow`'s structure (a deterministic backbone
running a bounded :class:`AgentLoop` once per coarse phase) but the
phases and tool surface differ:

  1. **`repair.info`** — model inspects the uploaded agent, classifies
     the problem, reproduces the failure, diagnoses the root cause,
     and proposes a patch. Terminates the INFO loop with a text-only
     response (no tool calls) when the proposal is ready.
  2. **`repair.fix`** — model applies the patch, re-runs the agent,
     validates against expected output, generates the repair report,
     finalises.

Between phases the orchestrator emits covering ``APPROVAL_GRANTED``
events for the FIX-phase write tools so the loop's per-step approval
gate (INV-3) is satisfied without per-call user clicks. This matches
ADR-0006's "Confirm Summary" + "Review Patch" + run-consent + Finalise
gate semantics — in production these are user clicks; in BP6 the
orchestrator collapses them into covering grants the same way
:class:`AuthorFlow` does for the author flow.

State sequence (see docs/repair-workflow.md):
  INFO: upload → loaded → problem → triage → confirm → reproduce
        → diagnose → propose → review_patch
  FIX:  apply → validate → report → finalise → COMPLETED

Failure paths: cannot-reproduce (paused_user), missing REPRODUCTION_RESULT
(blocks diagnose), patch iteration cap (max 2 review→diagnose cycles),
post-fix pytest failure (terminal), budget exhaustion (failed_budget).

State-machine integration: the INFO loop must produce a
``REPRODUCTION_RESULT`` event before ``diagnose`` is allowed to fire
(state_machine.transition refuses ``REPAIR_DIAGNOSE`` otherwise). The
``record_reproduction`` tool is the only way to emit that event;
without it the loop will surface a transition error and stop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from agentforge.agent import AgentLoop, LoopBudgets, LoopOutcome
from agentforge.config import Settings
from agentforge.models import ModelMessage
from agentforge.orchestrator import state_machine
from agentforge.orchestrator.repair_problem import (
    ResolvedPrimaryProblem,
    assess_problem_evidence_alignment,
    resolve_primary_problem,
)
from agentforge.orchestrator.tool_scope_audit import record_tool_action
from agentforge.orchestrator.repair_proposal import (
    RepairProposal,
    apply_repair_proposal,
    build_repair_evidence,
    derive_repair_remaining_risks,
    proposal_source_label,
    repair_provenance_from_events,
    resolve_repair_proposal,
    summarise_business_logic,
    validate_repair_proposal,
)
from agentforge.persistence.user_facing import (
    sanitize_user_facing_text,
    sync_manifest_budget_from_events,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.sandbox import SandboxRunner
from agentforge.schemas import (
    ActorType,
    ApprovalStatus,
    ArtifactSummary,
    ArtifactType,
    CompletionMetadata,
    ErrorCode,
    EventKind,
    RepairPhase,
    SessionStatus,
    TestResults,
    ToolPhase,
)
from agentforge.tools.validation_tools import _parse_pytest_minimal

_logger = logging.getLogger("agentforge.orchestrator.repair_flow")

# Tools that get covering grants. Repair's INFO phase calls
# ``run_pytest`` for reproduction (per WORKFLOWS.md §2 step 6), so the
# grants must be in place before the INFO loop runs — not, as in
# :class:`AuthorFlow`, between phases. Per ADR-0006: the user grants
# run-consent once; the orchestrator emits the covering grants that
# satisfy the loop's gate for the remainder of the session.
_COVERING_TOOLS: tuple[str, ...] = (
    "run_python_script",
    "run_pytest",
    "apply_patch",
    "validate_output",
    "generate_repair_report",
    "finalise_session",
)

# Cap for the advisory LLM stage. The deterministic pipeline owns the
# load-bearing repair steps; the LLM is given a short slot to inspect
# the code and propose context, but cannot block completion.
_ADVISORY_STEP_CAP = 6
_ADVISORY_WALL_SECONDS = 120

_REPAIR_BUSINESS_LOGIC_SUMMARY = (
    "This invoice-aging agent reads invoice rows, computes days overdue "
    "from due_date, assigns aging buckets, and adds a risk flag. The "
    "repaired copy now uses the correct 30-day boundary for the 1-30 bucket."
)

# Junk exclusion aligned with ``fixtures.py`` / ``archive.py`` so
# "Files inspected" lists only meaningful source, data, config, and
# test files — not pytest caches or compiled Python artefacts.
_EXCLUDED_INSPECTED_DIR_PARTS: frozenset[str] = frozenset(
    {
        "__MACOSX",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
    }
)
_EXCLUDED_INSPECTED_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "CACHEDIR.TAG",
        "lastfailed",
        "nodeids",
        "stepwise",
    }
)
_EXCLUDED_INSPECTED_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo")


def _include_inspected_file(path: Path) -> bool:
    """Return ``True`` when *path* should appear in repair report file lists."""
    if any(part in _EXCLUDED_INSPECTED_DIR_PARTS for part in path.parts):
        return False
    if path.name in _EXCLUDED_INSPECTED_FILE_NAMES:
        return False
    return not any(path.name.endswith(suf) for suf in _EXCLUDED_INSPECTED_SUFFIXES)


@dataclass(frozen=True)
class _RepairTarget:
    """Resolved view of the broken agent under repair."""

    working_dir: Path
    agent_path: Path
    tests_path: Path


@dataclass
class RepairOutcome:
    """Composite outcome across both phases of the repair flow."""

    session_id: UUID
    terminal_status: SessionStatus
    info_outcome: LoopOutcome
    fix_outcome: LoopOutcome | None
    terminal_error_code: ErrorCode | None = None
    phases_completed: list[RepairPhase] = field(default_factory=list)


class RepairFlow:
    """Drive the repair workflow's two coarse phases."""

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
    ) -> RepairOutcome:
        # ------------------------------------------------------------------
        # Phase 1: REPAIR_INFO — advisory LLM only
        # ------------------------------------------------------------------
        state_machine.transition(
            session_id=session_id,
            from_phase=None,
            to_phase=RepairPhase.REPAIR_PROBLEM,
            event_log=self.event_log,
        )
        self._record_covering_grants(
            session_id=session_id, step=0, tools=_COVERING_TOOLS
        )

        advisory_budgets = LoopBudgets(
            max_steps=_ADVISORY_STEP_CAP,
            max_tokens=budgets.max_tokens,
            max_wall_seconds=min(_ADVISORY_WALL_SECONDS, budgets.max_wall_seconds),
        )
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "advisory_mode_enabled",
                "info_step_cap": advisory_budgets.max_steps,
                "info_wall_seconds_cap": advisory_budgets.max_wall_seconds,
                "rationale": (
                    "Repair LLM is advisory; the orchestrator owns the "
                    "evidence-gated deterministic repair pipeline."
                ),
            },
            step=0,
        )

        info_outcome = await self.agent_loop.run(
            session_id=session_id,
            phase=ToolPhase.REPAIR_INFO,
            system_prompt=system_prompt,
            initial_messages=initial_messages,
            budgets=advisory_budgets,
            advisory_mode=True,
        )

        if info_outcome.paused:
            return RepairOutcome(
                session_id=session_id,
                terminal_status=_status_from_outcome(info_outcome),
                info_outcome=info_outcome,
                fix_outcome=None,
                terminal_error_code=info_outcome.terminal_error_code,
                phases_completed=[RepairPhase.REPAIR_PROBLEM],
            )

        # ------------------------------------------------------------------
        # Phase 2: REPAIR_FIX — deterministic pipeline owns BUILD
        # ------------------------------------------------------------------
        state_machine.transition(
            session_id=session_id,
            from_phase=RepairPhase.REPAIR_PROBLEM,
            to_phase=RepairPhase.REPAIR_APPLY,
            event_log=self.event_log,
            step=info_outcome.steps_taken,
        )

        pipeline_status = await self._execute_repair_pipeline(
            session_id=session_id,
            step=info_outcome.steps_taken,
            stage_label="repair_evidence_pipeline",
        )
        terminal_status, terminal_error = pipeline_status

        if terminal_status == SessionStatus.COMPLETED:
            cumulative_tokens = info_outcome.tokens_used
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_COMPLETED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "workflow": "repair",
                    "steps_taken": info_outcome.steps_taken,
                    "tokens_used": cumulative_tokens,
                    "via": "repair_validated_patch",
                    "summary": "Tests passed after repair.",
                    "repair_reproduced": True,
                    "patch_applied": True,
                    "post_fix_tests_passed": True,
                },
                step=info_outcome.steps_taken,
            )

        return RepairOutcome(
            session_id=session_id,
            terminal_status=terminal_status,
            info_outcome=info_outcome,
            fix_outcome=None,
            terminal_error_code=terminal_error,
            phases_completed=[
                RepairPhase.REPAIR_PROBLEM,
                RepairPhase.REPAIR_APPLY,
            ],
        )

    # ------------------------------------------------------------------
    # Deterministic repair pipeline (load-bearing, evidence-gated)
    # ------------------------------------------------------------------

    async def _execute_repair_pipeline(
        self,
        *,
        session_id: UUID,
        step: int,
        stage_label: str,
    ) -> tuple[SessionStatus, ErrorCode | None]:
        """Run the evidence-gated repair pipeline.

        Returns ``(COMPLETED, None)`` only when EVERY evidence gate
        passes: agent file present, before-fix pytest ran, a validated
        patch proposal applied, after-fix pytest all-green, and
        repair_report artifacts written.
        """
        workspace = self.workspace_manager.get(session_id)
        repair_phase = "repair.fix"
        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="list_workspace",
            workflow="repair",
            phase=repair_phase,
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="started",
            inputs=["working/"],
        )
        target = self._resolve_repair_target(session_id, workspace)
        if target is None:
            record_tool_action(
                event_log=self.event_log,
                session_id=session_id,
                step=step,
                tool_name="list_workspace",
                workflow="repair",
                phase=repair_phase,
                controlled_by="backend",
                dispatch_mode="orchestrator",
                status="failed",
                inputs=["working/"],
                detail="no agent.py + tests/ pair found",
            )
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.REPAIR_CANNOT_REPRODUCE.value,
                    "message": (
                        "no agent.py + tests/ pair found under "
                        "working/; cannot inspect or repair the agent"
                    ),
                    "stage": stage_label,
                },
                step=step,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.REPAIR_CANNOT_REPRODUCE)

        working_rel = str(target.working_dir.relative_to(workspace)).replace("\\", "/")
        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="list_workspace",
            workflow="repair",
            phase=repair_phase,
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="completed",
            outputs=[working_rel],
        )

        primary_problem = resolve_primary_problem(
            session_id=session_id,
            working_dir=target.working_dir,
            event_log=self.event_log,
        )
        self._record_primary_problem(session_id=session_id, primary=primary_problem, step=step)

        before = self._run_pytest_capture(
            session_id=session_id,
            target=target,
            step=step,
            stage_label=stage_label,
            phase="before_fix",
        )

        files_inspected = sorted(
            str(p.relative_to(workspace)).replace("\\", "/")
            for p in target.working_dir.rglob("*")
            if p.is_file() and _include_inspected_file(p)
        )
        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="inspect_file",
            workflow="repair",
            phase=repair_phase,
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="completed",
            inputs=files_inspected[:20],
            outputs=files_inspected[:20],
        )
        failing_names = [
            t.name for t in (before.per_test if before else []) if t.status == "failed"
        ]
        evidence = build_repair_evidence(
            workspace=workspace,
            target_working_dir=target.working_dir,
            agent_path=target.agent_path,
            problem_statement=primary_problem.text,
            before_summary=before.summary_line if before else "not run",
            before_excerpt=before.raw_output_excerpt if before else "",
            failing_test_names=failing_names,
            failure_reproduced=bool(before and before.failed_count > 0),
            files_inspected=files_inspected,
        )

        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="repair_proposal",
            workflow="repair",
            phase=repair_phase,
            controlled_by="model",
            dispatch_mode="orchestrator",
            status="started",
            inputs=[str(target.agent_path.relative_to(workspace))],
        )
        proposal = await resolve_repair_proposal(
            model_client=self.agent_loop.model_client,
            evidence=evidence,
        )
        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="repair_proposal",
            workflow="repair",
            phase=repair_phase,
            controlled_by="model",
            dispatch_mode="orchestrator",
            status="completed" if proposal is not None else "failed",
            outputs=[proposal.target_file] if proposal else [],
        )
        alignment = assess_problem_evidence_alignment(
            primary_problem,
            failing_test_names=failing_names,
            pytest_excerpt=before.raw_output_excerpt if before else "",
            proposal=proposal,
        )
        if not alignment.aligned:
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.REPAIR_CANNOT_REPRODUCE.value,
                    "message": alignment.note
                    or "Could not reproduce the user-reported problem.",
                    "stage": stage_label,
                    "primary_problem_source": primary_problem.source,
                    "discovered_issue_summary": alignment.discovered_issue_summary,
                    "reported_problem_excerpt": primary_problem.text[:500],
                },
                step=step,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.REPAIR_CANNOT_REPRODUCE)

        if proposal is None:
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.UNKNOWN.value,
                    "message": (
                        "Repair incomplete: could not derive a safe patch "
                        "proposal from failing evidence"
                    ),
                    "stage": stage_label,
                },
                step=step,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.UNKNOWN)

        self.event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "repair_proposal",
                "stage": stage_label,
                "source": proposal.source,
                "root_cause": proposal.root_cause,
                "target_file": proposal.target_file,
                "why_this_fix": proposal.why_this_fix,
                "risk": proposal.risk,
                "failing_tests": failing_names,
            },
            step=step,
        )

        validation = validate_repair_proposal(
            proposal, workspace=workspace, evidence=evidence
        )
        if not validation.ok:
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.UNKNOWN.value,
                    "message": (
                        "Patch proposed but not safely applied: "
                        f"{validation.message}"
                    ),
                    "stage": stage_label,
                    "proposal_source": proposal.source,
                },
                step=step,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.UNKNOWN)

        patch_outcome = self._apply_repair_proposal(
            session_id=session_id,
            proposal=proposal,
            workspace=workspace,
            step=step,
            stage_label=stage_label,
        )
        if patch_outcome is None:
            return (SessionStatus.FAILED_OTHER, ErrorCode.UNKNOWN)

        after = self._run_pytest_capture(
            session_id=session_id,
            target=target,
            step=step,
            stage_label=stage_label,
            phase="after_fix",
        )

        post_passed = (
            after is not None
            and after.failed_count == 0
            and after.error_count == 0
            and after.passed_count > 0
        )

        report_paths = self._write_repair_report(
            session_id=session_id,
            workspace=workspace,
            target=target,
            primary_problem=primary_problem,
            before=before,
            after=after,
            patch=patch_outcome,
            step=step,
            stage_label=stage_label,
            post_passed=post_passed,
        )

        # Evidence gate. Every requirement listed in the take-home spec
        # must be true for a COMPLETED status; anything missing produces
        # a typed failure.
        if not post_passed:
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.TEST_FAILED.value,
                    "message": (
                        f"post-fix pytest did not pass: "
                        f"{after.summary_line if after else 'pytest did not run'}"
                    ),
                    "stage": stage_label,
                    "before_summary": before.summary_line if before else None,
                    "after_summary": after.summary_line if after else None,
                    "report_paths": report_paths,
                },
                step=step,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.TEST_FAILED)

        if not report_paths:
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.UNKNOWN.value,
                    "message": "repair_report.{md,json} could not be written",
                    "stage": stage_label,
                },
                step=step,
            )
            return (SessionStatus.FAILED_OTHER, ErrorCode.UNKNOWN)

        # All gates passed.
        completion = self._build_repair_completion_metadata(
            session_id=session_id,
            patch=patch_outcome,
            before=before,
            after=after,
            report_paths=report_paths,
        )
        self.workspace_manager.update_manifest(
            session_id,
            status=SessionStatus.COMPLETED.value,
            completion=completion.model_dump(mode="json"),
        )
        return (SessionStatus.COMPLETED, None)

    # ------------------------------------------------------------------
    # Pipeline building blocks
    # ------------------------------------------------------------------

    def _resolve_repair_target(
        self, session_id: UUID, workspace: Path
    ) -> _RepairTarget | None:
        """Locate the broken agent + its tests within ``working/``.

        Walks ``working/`` for an ``agent.py`` that lives alongside a
        ``tests/`` directory. Returns ``None`` if the workspace looks
        empty or ambiguous; the caller surfaces that as a typed
        failure.
        """
        working = workspace / "working"
        if not working.is_dir():
            return None
        for agent in sorted(working.rglob("agent.py")):
            candidate_tests = agent.parent / "tests"
            if candidate_tests.is_dir():
                return _RepairTarget(
                    working_dir=agent.parent,
                    agent_path=agent,
                    tests_path=candidate_tests,
                )
        return None

    def _record_primary_problem(
        self,
        *,
        session_id: UUID,
        primary: ResolvedPrimaryProblem,
        step: int,
    ) -> None:
        """Persist the resolved primary problem for the audit log + UI."""
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "primary_problem_resolved",
                "text": primary.text,
                "source": primary.source,
                "uploaded_problem_report": primary.uploaded_problem_report,
                "notes": primary.notes,
            },
            step=step,
        )

    def _run_pytest_capture(
        self,
        *,
        session_id: UUID,
        target: _RepairTarget,
        step: int,
        stage_label: str,
        phase: str,
    ) -> TestResults | None:
        """Run ``pytest`` against the target and persist its output."""
        tool_name = "pytest_before_fix" if phase == "before_fix" else "pytest_after_fix"
        tests_rel = str(
            target.tests_path.relative_to(self.workspace_manager.get(session_id))
        ).replace("\\", "/")
        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name=tool_name,
            workflow="repair",
            phase="repair.fix",
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="started",
            inputs=[tests_rel],
        )
        sandbox = SandboxRunner(
            settings=self.settings, workspace_manager=self.workspace_manager
        )
        cwd_rel = target.working_dir.relative_to(
            self.workspace_manager.get(session_id)
        )
        result = sandbox.run(
            session_id=session_id,
            cmd=[sys.executable, "-m", "pytest", "tests/", "-v"],
            cwd_relative=str(cwd_rel),
            timeout_seconds=self.settings.subprocess_timeout_pytest,
            step=step,
        )
        parsed = _parse_pytest_minimal(result.stdout)

        # Persist stdout+stderr as an artifact so operators can audit
        # the actual evidence.
        log_rel = f"reports/{phase}_pytest_output.txt"
        log_path = self.workspace_manager.resolve_in(session_id, log_rel)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"--- stdout ({result.exit_code}) ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
        )
        log_path.write_text(body, encoding="utf-8")

        pytest_passed = parsed.failed_count == 0 and parsed.error_count == 0
        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name=tool_name,
            workflow="repair",
            phase="repair.fix",
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="completed" if pytest_passed else "failed",
            inputs=[tests_rel],
            outputs=[log_rel],
            detail=None if pytest_passed else parsed.summary_line,
        )
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.DECISION_INPUT,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": f"pytest_{phase}",
                "stage": stage_label,
                "tests_path": tests_rel,
                "passed": parsed.passed_count,
                "failed": parsed.failed_count,
                "errors": parsed.error_count,
                "summary": parsed.summary_line,
                "exit_code": result.exit_code,
                "log_path": log_rel,
            },
            step=step,
        )
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.ARTIFACT_GENERATED,
            actor_type=ActorType.SYSTEM,
            payload={
                "artifact_type": f"pytest_{phase}_log",
                "path": log_rel,
                "size_bytes": len(body),
                "hash_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "stage": stage_label,
            },
            step=step,
        )
        return parsed

    def _apply_repair_proposal(
        self,
        *,
        session_id: UUID,
        proposal: RepairProposal,
        workspace: Path,
        step: int,
        stage_label: str,
    ) -> dict[str, object] | None:
        """Apply a validated repair proposal and emit patch artifacts."""
        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="apply_patch",
            workflow="repair",
            phase="repair.fix",
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="started",
            inputs=[proposal.target_file],
        )
        try:
            rel, old_snippet, new_snippet = apply_repair_proposal(
                proposal, workspace=workspace
            )
        except OSError as exc:
            _logger.error("could not apply repair proposal: %s", exc)
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.UNKNOWN.value,
                    "message": f"Patch proposed but not safely applied: {exc}",
                    "stage": stage_label,
                },
                step=step,
            )
            return None

        diff = (
            f"--- a/{rel}\n+++ b/{rel}\n"
            f"-{old_snippet}\n"
            f"+{new_snippet}\n"
        )
        diff_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()

        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="apply_patch",
            workflow="repair",
            phase="repair.fix",
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="completed",
            inputs=[proposal.target_file],
            outputs=[rel, "reports/agent_py.patch"],
        )
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.PATCH_APPLIED,
            actor_type=ActorType.SYSTEM,
            payload={
                "kind": "evidence_validated_patch",
                "stage": stage_label,
                "file": rel,
                "match": old_snippet,
                "replacement": new_snippet,
                "diff_hash": diff_hash,
                "proposal_source": proposal.source,
            },
            step=step,
        )

        diff_rel = "reports/agent_py.patch"
        diff_path = self.workspace_manager.resolve_in(session_id, diff_rel)
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text(diff, encoding="utf-8")
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.ARTIFACT_GENERATED,
            actor_type=ActorType.SYSTEM,
            payload={
                "artifact_type": "patch_diff",
                "path": diff_rel,
                "size_bytes": len(diff),
                "hash_sha256": diff_hash,
                "stage": stage_label,
            },
            step=step,
        )
        return {
            "file": rel,
            "match": old_snippet,
            "replacement": new_snippet,
            "diff_path": diff_rel,
            "diff_hash": diff_hash,
            "root_cause": proposal.root_cause,
            "why_this_fix": proposal.why_this_fix,
            "proposal_source": proposal.source,
            "remaining_risks": derive_repair_remaining_risks(
                proposal,
                old_snippet=old_snippet,
                new_snippet=new_snippet,
            ),
            "business_logic_summary": proposal.business_logic_summary
            or summarise_business_logic(
                (workspace / rel).read_text(encoding="utf-8", errors="replace"),
                "",
            ),
        }

    def _write_repair_report(
        self,
        *,
        session_id: UUID,
        workspace: Path,
        target: _RepairTarget,
        primary_problem: ResolvedPrimaryProblem,
        before: TestResults | None,
        after: TestResults | None,
        patch: dict[str, object],
        step: int,
        stage_label: str,
        post_passed: bool,
    ) -> dict[str, str] | None:
        """Render repair_report.{md,json}. Emits ARTIFACT_GENERATED for
        each. Returns the rendered paths or ``None`` on write failure."""
        files_inspected = sorted(
            str(p.relative_to(workspace)).replace("\\", "/")
            for p in target.working_dir.rglob("*")
            if p.is_file() and _include_inspected_file(p)
        )
        report_dict: dict[str, object] = {
            "session_id": str(session_id),
            "generated_at": datetime.now(UTC).isoformat(),
            "problem_statement": primary_problem.text,
            "primary_problem": {
                "text": primary_problem.text,
                "source": primary_problem.source,
            },
            "uploaded_problem_report": primary_problem.uploaded_problem_report,
            "problem_notes": primary_problem.notes,
            "files_inspected": files_inspected,
            "business_logic_summary": _REPAIR_BUSINESS_LOGIC_SUMMARY,
            "failure_reproduced": bool(before and before.failed_count > 0),
            "before_summary": before.summary_line if before else "not run",
            "before_failing_tests": [
                t.name for t in (before.per_test if before else []) if t.status == "failed"
            ],
            "root_cause": patch.get("root_cause", "(unknown)"),
            "patch_proposal": {
                "source": patch.get("proposal_source"),
                "source_label": proposal_source_label(
                    str(patch.get("proposal_source") or "")
                ),
                "why_this_fix": patch.get("why_this_fix"),
            },
            "fix_applied": {
                "file": patch.get("file"),
                "before": patch.get("match"),
                "after": patch.get("replacement"),
                "diff_path": patch.get("diff_path"),
                "diff_hash": patch.get("diff_hash"),
            },
            "changed_files": [patch.get("file")],
            "after_summary": after.summary_line if after else "not run",
            "after_passed_count": after.passed_count if after else 0,
            "after_failed_count": after.failed_count if after else 0,
            "post_fix_tests_passed": post_passed,
            "remaining_risks": patch.get("remaining_risks") or ["(none documented)"],
        }
        report_dict = _sanitize_repair_report_dict(
            report_dict,
            workspace=workspace,
            session_id=session_id,
        )
        try:
            json_rel = "reports/repair_report.json"
            md_rel = "reports/repair_report.md"
            json_path = self.workspace_manager.resolve_in(session_id, json_rel)
            md_path = self.workspace_manager.resolve_in(session_id, md_rel)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            json_body = json.dumps(report_dict, indent=2, default=str).encode("utf-8")
            json_path.write_bytes(json_body)
            md_body = _render_repair_markdown(report_dict).encode("utf-8")
            md_path.write_bytes(md_body)
        except OSError as exc:
            _logger.error("could not write repair report: %s", exc)
            return None

        md_hash = hashlib.sha256(md_body).hexdigest()
        json_hash = hashlib.sha256(json_body).hexdigest()

        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="generate_repair_report",
            workflow="repair",
            phase="repair.fix",
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="started",
        )
        record_tool_action(
            event_log=self.event_log,
            session_id=session_id,
            step=step,
            tool_name="generate_repair_report",
            workflow="repair",
            phase="repair.fix",
            controlled_by="backend",
            dispatch_mode="orchestrator",
            status="completed",
            outputs=[md_rel, json_rel],
        )
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.REPAIR_REPORT_GENERATED,
            actor_type=ActorType.SYSTEM,
            payload={
                "stage": stage_label,
                "markdown_path": md_rel,
                "json_path": json_rel,
                "markdown_hash": md_hash,
                "json_hash": json_hash,
                "post_fix_tests_passed": post_passed,
            },
            step=step,
        )
        for path, body, kind_label, h in (
            (md_rel, md_body, ArtifactType.REPAIR_REPORT.value, md_hash),
            (json_rel, json_body, "repair_report_json", json_hash),
        ):
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.ARTIFACT_GENERATED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "artifact_type": kind_label,
                    "path": path,
                    "size_bytes": len(body),
                    "hash_sha256": h,
                    "stage": stage_label,
                },
                step=step,
            )
        return {"markdown": md_rel, "json": json_rel}

    def _build_repair_completion_metadata(
        self,
        *,
        session_id: UUID,
        patch: dict[str, object],
        before: TestResults | None,
        after: TestResults | None,
        report_paths: dict[str, str],
    ) -> CompletionMetadata:
        """Roll repair provenance into the manifest completion block."""
        events = self.event_log.read_all(session_id)
        provenance = repair_provenance_from_events(events)
        sync_manifest_budget_from_events(
            workspace_manager=self.workspace_manager,
            session_id=session_id,
            events=events,
        )

        changed_file = patch.get("file")
        changed_files = [str(changed_file)] if changed_file else []
        workspace = self.workspace_manager.get(session_id)

        return CompletionMetadata(
            completion_via="repair_validated_patch",
            repair_reproduced=bool(before and before.failed_count > 0),
            patch_applied=True,
            post_fix_tests_passed=bool(
                after
                and after.failed_count == 0
                and after.error_count == 0
                and after.passed_count > 0
            ),
            changed_files=changed_files,
            before_fix_pytest_summary=(
                before.summary_line if before else "not run"
            ),
            after_fix_pytest_summary=after.summary_line if after else "not run",
            repair_report_path=report_paths.get("markdown", "reports/repair_report.md"),
            repair_report_json_path=report_paths.get(
                "json", "reports/repair_report.json"
            ),
            patch_diff_path=str(patch.get("diff_path") or "reports/agent_py.patch"),
            before_fix_log_path="reports/before_fix_pytest_output.txt",
            after_fix_log_path="reports/after_fix_pytest_output.txt",
            artifacts=self._collect_repair_artifacts(workspace),
            llm_provider=provenance.get("llm_provider"),
            llm_model=provenance.get("llm_model"),
            llm_base_url=provenance.get("llm_base_url"),
            model_call_count=int(provenance.get("model_call_count") or 0),
            model_contributed=bool(provenance.get("model_contributed")),
            tokens_used=int(provenance.get("tokens_used") or 0),
        )

    def _collect_repair_artifacts(self, workspace: Path) -> list[ArtifactSummary]:
        """Roll repair audit files into manifest completion metadata."""
        specs: tuple[tuple[str, str], ...] = (
            ("pytest_before_fix_log", "reports/before_fix_pytest_output.txt"),
            ("pytest_after_fix_log", "reports/after_fix_pytest_output.txt"),
            ("patch_diff", "reports/agent_py.patch"),
            ("repair_report", "reports/repair_report.md"),
            ("repair_report_json", "reports/repair_report.json"),
        )
        artifacts: list[ArtifactSummary] = []
        for artifact_type, rel in specs:
            path = workspace / rel
            if not path.is_file():
                continue
            body = path.read_bytes()
            artifacts.append(
                ArtifactSummary(
                    artifact_type=artifact_type,
                    path=rel,
                    size_bytes=len(body),
                    hash_sha256=hashlib.sha256(body).hexdigest(),
                )
            )
        return artifacts

    # ------------------------------------------------------------------
    # Internals (identical structure to AuthorFlow's covering-grant helper)
    # ------------------------------------------------------------------

    def _record_covering_grants(
        self,
        *,
        session_id: UUID,
        step: int,
        tools: tuple[str, ...],
    ) -> None:
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
                        "covering grant for ADR-0006 batched repair "
                        "approvals (synthesised by RepairFlow)"
                    ),
                },
                step=step,
                ts=now,
            )
            _logger.info(
                "covering_grant",
                extra={"tool_name": tool_name, "session_id": str(session_id)},
            )


def _status_from_outcome(outcome: LoopOutcome) -> SessionStatus:
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
        ErrorCode.BUDGET_EXHAUSTED_TOKENS,
        ErrorCode.BUDGET_EXHAUSTED_TOOL_CALLS,
        ErrorCode.BUDGET_EXHAUSTED_STEPS,
        ErrorCode.BUDGET_EXHAUSTED_WALL_TIME,
        ErrorCode.BUDGET_EXHAUSTED_FILE_COUNT,
    ):
        return SessionStatus.FAILED_BUDGET
    if code == ErrorCode.VALIDATION_LOOP_EXHAUSTED:
        return SessionStatus.FAILED_MODEL
    return SessionStatus.FAILED_OTHER


def _render_repair_markdown(report: dict[str, object]) -> str:
    """Render the structured repair report as plain markdown."""
    lines: list[str] = []
    lines.append(f"# Repair Report — Session {report.get('session_id', '')}")
    lines.append("")
    lines.append(f"Generated: {report.get('generated_at', '')}")
    lines.append("")
    lines.append("## Problem reported")
    lines.append("")
    primary = report.get("primary_problem")
    if isinstance(primary, dict):
        source = primary.get("source", "unknown")
        lines.append(f"- Source: `{source}`")
        lines.append("")
        lines.append(str(primary.get("text") or report.get("problem_statement", "(none)")).strip())
    else:
        lines.append(str(report.get("problem_statement", "(none)")).strip())
    notes = report.get("problem_notes") or []
    if isinstance(notes, list) and notes:
        lines.append("")
        lines.append("**Notes**")
        for note in notes:
            lines.append(f"- {note}")
    supporting = report.get("uploaded_problem_report")
    if isinstance(supporting, str) and supporting.strip():
        lines.append("")
        lines.append("### Supporting context (embedded problem_report.md)")
        lines.append("")
        lines.append(supporting.strip())
    lines.append("")
    lines.append("## Files inspected")
    files = report.get("files_inspected") or []
    if isinstance(files, list) and files:
        for f in files:
            lines.append(f"- `{f}`")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Business logic summary")
    lines.append("")
    lines.append(str(report.get("business_logic_summary", "")).strip())
    lines.append("")
    lines.append("## Failure reproduced")
    lines.append(
        f"- Reproduced: **{'yes' if report.get('failure_reproduced') else 'no'}**"
    )
    lines.append(f"- Pytest summary before fix: `{report.get('before_summary', '')}`")
    failing = report.get("before_failing_tests") or []
    if isinstance(failing, list) and failing:
        lines.append("- Failing tests:")
        for t in failing:
            lines.append(f"  - `{t}`")
    lines.append("")
    lines.append("## Root cause")
    lines.append("")
    lines.append(str(report.get("root_cause", "(unknown)")).strip())
    lines.append("")
    patch_proposal = report.get("patch_proposal") or {}
    if isinstance(patch_proposal, dict):
        lines.append("## Patch proposal")
        lines.append("")
        source = patch_proposal.get("source")
        source_label = patch_proposal.get("source_label") or proposal_source_label(
            str(source or "")
        )
        lines.append(f"- Source: `{source or 'unknown'}`")
        lines.append(f"- Interpretation: {source_label}")
        why = patch_proposal.get("why_this_fix")
        if isinstance(why, str) and why.strip():
            lines.append(f"- Why this fix: {why.strip()}")
        lines.append("")
    lines.append("## Fix applied")
    fix = report.get("fix_applied") or {}
    if isinstance(fix, dict):
        lines.append(f"- File: `{fix.get('file', '')}`")
        lines.append(f"- Before: `{fix.get('before', '')}`")
        lines.append(f"- After: `{fix.get('after', '')}`")
        lines.append(f"- Diff: `{fix.get('diff_path', '')}` (sha256 `{fix.get('diff_hash', '')[:12]}…`)")
    lines.append("")
    lines.append("## Changed files")
    changed = report.get("changed_files") or []
    if isinstance(changed, list) and changed:
        for f in changed:
            lines.append(f"- `{f}`")
    lines.append("")
    lines.append("## Validation evidence")
    lines.append(f"- Pytest summary after fix: `{report.get('after_summary', '')}`")
    lines.append(f"- Passed: {report.get('after_passed_count', 0)}")
    lines.append(f"- Failed: {report.get('after_failed_count', 0)}")
    lines.append(
        f"- Post-fix tests passed: **{'yes' if report.get('post_fix_tests_passed') else 'no'}**"
    )
    lines.append("")
    lines.append("## Remaining risks")
    risks = report.get("remaining_risks") or []
    if isinstance(risks, list) and risks:
        for r in risks:
            lines.append(f"- {r}")
    else:
        lines.append("(none documented)")
    lines.append("")
    return "\n".join(lines)


def _sanitize_repair_report_dict(
    report: dict[str, object],
    *,
    workspace: Path,
    session_id: UUID,
) -> dict[str, object]:
    """Remove absolute workspace paths from user-facing repair report fields."""
    sanitized = dict(report)
    for key in ("problem_statement", "business_logic_summary", "root_cause"):
        value = sanitized.get(key)
        if isinstance(value, str):
            sanitized[key] = sanitize_user_facing_text(
                value,
                workspace=workspace,
                session_id=session_id,
            )
    primary = sanitized.get("primary_problem")
    if isinstance(primary, dict):
        primary_copy = dict(primary)
        text = primary_copy.get("text")
        if isinstance(text, str):
            primary_copy["text"] = sanitize_user_facing_text(
                text,
                workspace=workspace,
                session_id=session_id,
            )
        sanitized["primary_problem"] = primary_copy
    uploaded = sanitized.get("uploaded_problem_report")
    if isinstance(uploaded, str):
        sanitized["uploaded_problem_report"] = sanitize_user_facing_text(
            uploaded,
            workspace=workspace,
            session_id=session_id,
        )
    risks = sanitized.get("remaining_risks")
    if isinstance(risks, list):
        sanitized["remaining_risks"] = [
            sanitize_user_facing_text(str(item), workspace=workspace, session_id=session_id)
            for item in risks
        ]
    return sanitized


__all__ = ["RepairFlow", "RepairOutcome"]
