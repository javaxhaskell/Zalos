"""HTTP-side orchestrator supervisor (BP8).

This module is the bridge between the FastAPI ``POST /sessions/{id}/run``
endpoint and the in-process :class:`AuthorFlow` / :class:`RepairFlow`.
The endpoint returns ``202 Accepted`` immediately; the flow runs on a
background ``asyncio.Task`` tracked on ``app.state.run_tasks``.

Responsibilities split across this module and the router:

  * Router: idempotency gate (404 / 409 RUNNING with active task / 409
    COMPLETED), request validation, ``mark_running``, orphan recovery
    when the row says RUNNING but no task owns it.
  * Runner (here): build initial messages from prior FILE_UPLOADED
    events + the user's free-text input, pick the system prompt, run
    the flow, persist the terminal status to the DB row, and never
    let an unhandled exception leak (anything escaping the flow becomes
    a ``WORKFLOW_FAILED`` event + ``FAILED_OTHER`` row status).

INV-1 + INV-2: the runner never dispatches a tool directly; it
constructs the loop, hands the model client a phase-filtered tool set,
and lets the loop's executor do the rest. INV-7: idempotency is
enforced at the router boundary (single active run per session).

Workflow dispatch:
  Workflow.AUTHOR → AuthorFlow → author_custom_build.execute_custom_workflow_pipeline
  Workflow.REPAIR → RepairFlow (repair_flow.py, two-phase INFO/FIX)

Terminal persistence: _run_flow catches unhandled exceptions → WORKFLOW_FAILED
event + FAILED_OTHER. Orphan recovery reconciles RUNNING rows after restart
when no background task owns the session (docs/failure-modes.md).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker

from agentforge.agent import AgentLoop, LoopBudgets
from agentforge.agent.prompts import load_author_prompt, load_repair_prompt
from agentforge.config import Settings
from agentforge.models import ModelClient, ModelMessage, TextBlock
from agentforge.orchestrator.author_flow import AuthorFlow
from agentforge.orchestrator.repair_flow import RepairFlow
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.models import SessionRow
from agentforge.persistence.live_budget_sync import (
    reset_live_budget_session_store,
    set_live_budget_session_store,
)
from agentforge.persistence.session_store import SessionNotFoundError, SessionStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    ErrorCode,
    EventKind,
    Session,
    SessionStatus,
    Workflow,
)
from agentforge.tools import ToolRegistry

_logger = logging.getLogger("agentforge.orchestrator.runner")

_ORPHAN_RECOVERY_MESSAGE = (
    "The workflow was interrupted before it finished. "
    "No active background task owns this session."
)

_USER_CANCEL_MESSAGE = "The workflow was cancelled by the user."

CANCELLABLE_SESSION_STATUSES = frozenset(
    {
        SessionStatus.RUNNING,
        SessionStatus.PAUSED_USER,
        SessionStatus.PAUSED_APPROVAL,
    }
)


def get_run_tasks(app: FastAPI) -> dict[UUID, asyncio.Task[None]]:
    """Return (creating on first use) the per-session task registry.

    Stored on ``app.state.run_tasks`` so the lifespan can cancel
    in-flight tasks on shutdown and the router can distinguish a live
    run from an orphaned ``RUNNING`` row.
    """
    tasks = getattr(app.state, "run_tasks", None)
    if tasks is None:
        tasks = {}
        app.state.run_tasks = tasks
    return tasks


def has_active_run_task(app: FastAPI, session_id: UUID) -> bool:
    """True when a background flow task for ``session_id`` is still running."""
    task = get_run_tasks(app).get(session_id)
    return task is not None and not task.done()


def _get_user_cancelled_sessions(app: FastAPI) -> set[UUID]:
    """Sessions the user cancelled via ``POST /sessions/{id}/cancel``."""
    sessions = getattr(app.state, "user_cancelled_sessions", None)
    if sessions is None:
        sessions = set()
        app.state.user_cancelled_sessions = sessions
    return sessions


def spawn_flow_task(
    *,
    app: FastAPI,
    session_id: UUID,
    workflow: Workflow,
    user_message: str | None,
    template_hint: str | None,
    settings: Settings,
    model_client: ModelClient,
    registry: ToolRegistry,
    workspace_manager: WorkspaceManager,
    db_session_factory: sessionmaker[DBSession],
) -> asyncio.Task[None]:
    """Schedule the flow on an asyncio task and register it on app.state.

    The task is owned by ``app.state.run_tasks[session_id]``. The
    caller has already validated the idempotency gate and flipped the
    row to RUNNING; this function never touches the SessionStore on
    the caller's request-scoped DB session.
    """
    tasks = get_run_tasks(app)

    coro = _run_flow(
        app=app,
        session_id=session_id,
        workflow=workflow,
        user_message=user_message,
        template_hint=template_hint,
        settings=settings,
        model_client=model_client,
        registry=registry,
        workspace_manager=workspace_manager,
        db_session_factory=db_session_factory,
    )
    task = asyncio.create_task(coro, name=f"flow-{session_id}")

    def _on_done(t: asyncio.Task[None]) -> None:
        tasks.pop(session_id, None)
        if t.cancelled():
            _logger.warning("flow task cancelled", extra={"session_id": str(session_id)})
            return
        exc = t.exception()
        if exc is not None:
            # _run_flow already records WORKFLOW_FAILED + FAILED_OTHER in
            # its own try/except. Any exception escaping here is a bug
            # in the supervisor itself; log loudly.
            _logger.error(
                "flow task escaped its handler",
                exc_info=exc,
                extra={"session_id": str(session_id)},
            )

    task.add_done_callback(_on_done)
    tasks[session_id] = task
    return task


async def cancel_all_run_tasks(app: FastAPI) -> None:
    """Cancel every in-flight flow task and wait for cleanup."""
    tasks = list(get_run_tasks(app).values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def cancel_session_workflow(
    *,
    app: FastAPI,
    session_id: UUID,
    db_session_factory: sessionmaker[DBSession],
    workspace_manager: WorkspaceManager,
) -> Session:
    """Cancel an in-progress workflow and mark the session terminal.

    When a background task owns the session the task is cancelled and
    allowed to persist ``USER_ABANDONED``. Paused sessions (no active
    task) are marked terminal directly.
    """
    db = db_session_factory()
    try:
        event_log = EventLog(workspace_manager)
        session_store = SessionStore(
            db=db,
            workspace_manager=workspace_manager,
            event_log=event_log,
        )
        session = session_store.get_session(session_id)
        if session.status not in CANCELLABLE_SESSION_STATUSES:
            msg = (
                f"session {session_id} cannot be cancelled from status "
                f"{session.status.value!r}"
            )
            raise ValueError(msg)

        user_cancelled = _get_user_cancelled_sessions(app)
        user_cancelled.add(session_id)
        try:
            task = get_run_tasks(app).get(session_id)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            else:
                _append_workflow_user_cancelled(
                    event_log,
                    session_id,
                    step=session.current_step,
                )
                session_store.mark_terminal(
                    session_id=session_id,
                    status=SessionStatus.FAILED_OTHER,
                    terminal_error_code=ErrorCode.USER_ABANDONED,
                )
        finally:
            user_cancelled.discard(session_id)

        return session_store.get_session(session_id)
    finally:
        db.close()


def _terminal_failure_from_events(
    events: list,
) -> tuple[SessionStatus, ErrorCode] | None:
    """Return the latest recorded workflow failure, if any."""
    for event in reversed(events):
        if event.kind != EventKind.WORKFLOW_FAILED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        raw_code = payload.get("error_code")
        code = ErrorCode.UNKNOWN
        if isinstance(raw_code, str):
            try:
                code = ErrorCode(raw_code)
            except ValueError:
                code = ErrorCode.UNKNOWN
        if code in (
            ErrorCode.BUDGET_EXHAUSTED_STEPS,
            ErrorCode.BUDGET_EXHAUSTED_TOKENS,
            ErrorCode.BUDGET_EXHAUSTED_WALL_TIME,
            ErrorCode.BUDGET_EXHAUSTED_TOOL_CALLS,
            ErrorCode.BUDGET_EXHAUSTED_FILE_COUNT,
        ):
            return SessionStatus.FAILED_BUDGET, code
        if code == ErrorCode.VALIDATION_LOOP_EXHAUSTED:
            return SessionStatus.FAILED_MODEL, code
        return SessionStatus.FAILED_OTHER, code
    return None


def recover_orphaned_running_session(
    *,
    session_id: UUID,
    db_session_factory: sessionmaker[DBSession],
    workspace_manager: WorkspaceManager,
    app: FastAPI | None = None,
    message: str = _ORPHAN_RECOVERY_MESSAGE,
) -> Session | None:
    """Mark a ``RUNNING`` session terminal when no background task owns it.

    When the event log already contains ``workflow_failed``, the row is
    reconciled to that terminal failure instead of recording a generic
    ``workflow_interrupted`` (preserves provenance and validation errors).

    Returns the updated session row, or ``None`` if recovery was skipped
    (unknown session, not ``RUNNING``, or an active task is registered).
    """
    if app is not None and has_active_run_task(app, session_id):
        return None

    db = db_session_factory()
    try:
        event_log = EventLog(workspace_manager)
        session_store = SessionStore(
            db=db,
            workspace_manager=workspace_manager,
            event_log=event_log,
        )
        try:
            session = session_store.get_session(session_id)
        except SessionNotFoundError:
            return None
        if session.status != SessionStatus.RUNNING:
            return None

        events = event_log.read_all(session_id)
        prior_failure = _terminal_failure_from_events(events)
        if prior_failure is not None:
            terminal_status, terminal_error = prior_failure
        else:
            _append_workflow_interrupted(event_log, session_id, message)
            terminal_status = SessionStatus.FAILED_OTHER
            terminal_error = ErrorCode.WORKFLOW_INTERRUPTED

        _finalize_flow_terminal(
            session_store=session_store,
            event_log=event_log,
            session_id=session_id,
            terminal_status=terminal_status,
            terminal_error=terminal_error,
        )
        _logger.warning(
            "recovered orphaned running session",
            extra={
                "session_id": str(session_id),
                "terminal_error": terminal_error.value,
            },
        )
        return session_store.get_session(session_id)
    finally:
        db.close()


def recover_all_orphaned_running_sessions(
    *,
    db_session_factory: sessionmaker[DBSession],
    workspace_manager: WorkspaceManager,
) -> int:
    """Fail every DB row stuck in ``RUNNING`` after process restart."""
    db = db_session_factory()
    try:
        rows = (
            db.query(SessionRow)
            .filter(SessionRow.status == SessionStatus.RUNNING.value)
            .all()
        )
        session_ids = [UUID(row.id) for row in rows]
    finally:
        db.close()

    recovered = 0
    for session_id in session_ids:
        if (
            recover_orphaned_running_session(
                session_id=session_id,
                db_session_factory=db_session_factory,
                workspace_manager=workspace_manager,
                message=(
                    "The workflow was interrupted when the API restarted. "
                    "Start a new run to continue."
                ),
            )
            is not None
        ):
            recovered += 1
    return recovered


async def _run_flow(
    *,
    app: FastAPI | None,
    session_id: UUID,
    workflow: Workflow,
    user_message: str | None,
    template_hint: str | None,
    settings: Settings,
    model_client: ModelClient,
    registry: ToolRegistry,
    workspace_manager: WorkspaceManager,
    db_session_factory: sessionmaker[DBSession],
) -> None:
    """Run the workflow flow and persist its terminal status.

    Builds a fresh DB session for the task (the HTTP request's session
    has already closed by the time this runs). All persistence side-
    effects — manifest mutations, terminal-row updates, event log —
    flow through that session.
    """
    db = db_session_factory()
    event_log = EventLog(workspace_manager)
    session_store = SessionStore(
        db=db,
        workspace_manager=workspace_manager,
        event_log=event_log,
    )
    budget_store_token = set_live_budget_session_store(session_store)
    terminal_status: SessionStatus | None = None
    terminal_error: ErrorCode | None = None
    wall_total = 0
    try:
        if workflow == Workflow.REPAIR and user_message and user_message.strip():
            event_log.append(
                session_id=session_id,
                kind=EventKind.DECISION_INPUT,
                actor_type=ActorType.USER,
                payload={
                    "kind": "repair_user_problem",
                    "text": user_message.strip(),
                },
                step=0,
            )
        if workflow == Workflow.AUTHOR and user_message and user_message.strip():
            event_log.append(
                session_id=session_id,
                kind=EventKind.DECISION_INPUT,
                actor_type=ActorType.USER,
                payload={
                    "kind": "author_user_workflow",
                    "text": user_message.strip(),
                },
                step=0,
            )

        initial_messages = _build_initial_messages(
            session_id=session_id,
            workflow=workflow,
            user_message=user_message,
            template_hint=template_hint,
            event_log=event_log,
        )

        system_prompt = (
            load_author_prompt()
            if workflow == Workflow.AUTHOR
            else load_repair_prompt()
        )
        budgets = LoopBudgets(
            max_steps=(
                settings.budget_steps_author
                if workflow == Workflow.AUTHOR
                else settings.budget_steps_repair
            ),
            max_tokens=settings.budget_tokens,
            max_wall_seconds=settings.budget_wall_seconds,
        )

        loop = AgentLoop(
            registry=registry,
            model_client=model_client,
            idempotency_store=IdempotencyStore(db=db),
            event_log=event_log,
            workspace_manager=workspace_manager,
            settings=settings,
        )

        try:
            if workflow == Workflow.AUTHOR:
                flow = AuthorFlow(
                    agent_loop=loop,
                    event_log=event_log,
                    workspace_manager=workspace_manager,
                    settings=settings,
                )
                author_outcome = await flow.run(
                    session_id=session_id,
                    system_prompt=system_prompt,
                    initial_messages=initial_messages,
                    budgets=budgets,
                )
                terminal_status = author_outcome.terminal_status
                terminal_error = author_outcome.terminal_error_code
                wall_total = int(
                    author_outcome.info_outcome.wall_seconds
                    + (
                        author_outcome.build_outcome.wall_seconds
                        if author_outcome.build_outcome
                        else 0.0
                    )
                )
            else:
                repair_flow = RepairFlow(
                    agent_loop=loop,
                    event_log=event_log,
                    workspace_manager=workspace_manager,
                    settings=settings,
                )
                repair_outcome = await repair_flow.run(
                    session_id=session_id,
                    system_prompt=system_prompt,
                    initial_messages=initial_messages,
                    budgets=budgets,
                )
                terminal_status = repair_outcome.terminal_status
                terminal_error = repair_outcome.terminal_error_code
                wall_total = int(
                    repair_outcome.info_outcome.wall_seconds
                    + (
                        repair_outcome.fix_outcome.wall_seconds
                        if repair_outcome.fix_outcome
                        else 0.0
                    )
                )
        except asyncio.CancelledError:
            cancel_step = 0
            try:
                cancel_step = session_store.get_session(session_id).current_step
            except SessionNotFoundError:
                pass
            if app is not None and session_id in _get_user_cancelled_sessions(app):
                _append_workflow_user_cancelled(
                    event_log,
                    session_id,
                    step=cancel_step,
                )
                terminal_status = SessionStatus.FAILED_OTHER
                terminal_error = ErrorCode.USER_ABANDONED
            else:
                _append_workflow_interrupted(
                    event_log,
                    session_id,
                    "The workflow task was cancelled before it finished.",
                )
                terminal_status = SessionStatus.FAILED_OTHER
                terminal_error = ErrorCode.WORKFLOW_INTERRUPTED
            raise
        except Exception as exc:  # noqa: BLE001 — supervisor MUST absorb
            _logger.exception(
                "flow raised; recording FAILED_OTHER",
                extra={"session_id": str(session_id)},
            )
            event_log.append(
                session_id=session_id,
                kind=EventKind.WORKFLOW_FAILED,
                actor_type=ActorType.SYSTEM,
                payload={
                    "error_code": ErrorCode.UNKNOWN.value,
                    "message": f"flow task aborted: {type(exc).__name__}: {exc}",
                },
                step=0,
            )
            terminal_status = SessionStatus.FAILED_OTHER
            terminal_error = ErrorCode.UNKNOWN
    finally:
        reset_live_budget_session_store(budget_store_token)
        if terminal_status is not None:
            try:
                _finalize_flow_terminal(
                    session_store=session_store,
                    event_log=event_log,
                    session_id=session_id,
                    terminal_status=terminal_status,
                    terminal_error=terminal_error,
                    wall_total=wall_total,
                )
            except Exception:
                _logger.exception(
                    "failed to persist terminal flow status",
                    extra={"session_id": str(session_id)},
                )
        db.close()


def _append_workflow_interrupted(
    event_log: EventLog,
    session_id: UUID,
    message: str,
) -> None:
    event_log.append(
        session_id=session_id,
        kind=EventKind.WORKFLOW_FAILED,
        actor_type=ActorType.SYSTEM,
        payload={
            "error_code": ErrorCode.WORKFLOW_INTERRUPTED.value,
            "message": message,
        },
        step=0,
    )


def _append_workflow_user_cancelled(
    event_log: EventLog,
    session_id: UUID,
    *,
    step: int = 0,
) -> None:
    event_log.append(
        session_id=session_id,
        kind=EventKind.WORKFLOW_FAILED,
        actor_type=ActorType.USER,
        payload={
            "error_code": ErrorCode.USER_ABANDONED.value,
            "message": _USER_CANCEL_MESSAGE,
            "via": "http_endpoint",
        },
        step=step,
    )


def _finalize_flow_terminal(
    *,
    session_store: SessionStore,
    event_log: EventLog,
    session_id: UUID,
    terminal_status: SessionStatus,
    terminal_error: ErrorCode | None,
    wall_total: int = 0,
) -> None:
    """Persist cumulative budgets and the terminal row status."""
    full_events = event_log.read_all(session_id)
    cumulative_tokens = 0
    cumulative_tool_calls = 0
    cumulative_max_step = -1
    for evt in full_events:
        if evt.kind == EventKind.MODEL_CALLED:
            usage = evt.payload.get("usage") or {}
            cumulative_tokens += int(usage.get("input_tokens") or 0)
            cumulative_tokens += int(usage.get("output_tokens") or 0)
        if evt.kind == EventKind.TOOL_INVOKED:
            cumulative_tool_calls += 1
        if isinstance(evt.step, int) and evt.step > cumulative_max_step:
            cumulative_max_step = evt.step
    cumulative_steps = cumulative_max_step + 1 if cumulative_max_step >= 0 else 0
    if full_events:
        first_ts = full_events[0].ts
        last_ts = full_events[-1].ts
        wall_from_events = max(0, int((last_ts - first_ts).total_seconds()))
        wall_total = max(wall_total, wall_from_events)
    session_store.update_budget(
        session_id=session_id,
        tokens_used=cumulative_tokens,
        tool_calls_used=cumulative_tool_calls,
        steps_used=cumulative_steps,
        wall_seconds_used=wall_total,
    )
    session_store.mark_terminal(
        session_id=session_id,
        status=terminal_status,
        terminal_error_code=terminal_error,
    )


def _build_initial_messages(
    *,
    session_id: UUID,
    workflow: Workflow,
    user_message: str | None,
    template_hint: str | None,
    event_log: EventLog,
) -> list[ModelMessage]:
    """Compose the model's first ``user`` turn.

    Surfaces the workflow context the wizard gathered before clicking
    Run: uploaded file list, the free-text description, and the
    template hint. Wrapped in ``<user_message>`` and ``<file>``
    delimiters per INV-10 (uploaded/user content is data, never
    instructions).
    """
    upload_lines: list[str] = []
    answer_lines: list[str] = []
    for evt in event_log.read_all(session_id):
        if evt.kind != EventKind.FILE_UPLOADED:
            if evt.kind == EventKind.ANSWER_RECEIVED:
                answer = evt.payload.get("answer")
                question_event_id = evt.payload.get("question_event_id")
                if isinstance(answer, str):
                    if isinstance(question_event_id, str):
                        answer_lines.append(
                            f"- question_event_id={question_event_id}: {answer}"
                        )
                    else:
                        answer_lines.append(f"- {answer}")
            continue
        filename = evt.payload.get("filename", "<unknown>")
        size = evt.payload.get("size_bytes")
        rel = evt.payload.get("relative_path", f"uploads/{filename}")
        if size is not None:
            upload_lines.append(f"- {rel} ({size} bytes)")
        else:
            upload_lines.append(f"- {rel}")

    flow_label = "author" if workflow == Workflow.AUTHOR else "repair"
    parts: list[str] = [
        f"You are starting the {flow_label} workflow for this session.",
    ]
    if upload_lines:
        parts.append(
            "<uploaded_files>\n"
            + "\n".join(upload_lines)
            + "\n</uploaded_files>"
        )
    if template_hint:
        parts.append(
            "<template_hint>\n"
            f"The user picked the '{template_hint}' template from the wizard.\n"
            "</template_hint>"
        )
    if user_message:
        parts.append(
            "<user_message>\n"
            f"{user_message.strip()}\n"
            "</user_message>"
        )
    if answer_lines:
        parts.append(
            "<prior_user_answers>\n"
            + "\n".join(answer_lines)
            + "\n</prior_user_answers>"
        )
    if not upload_lines and not user_message:
        parts.append(
            "No files have been uploaded yet. Begin by listing the "
            "workspace and asking the user for input."
        )

    text = "\n\n".join(parts)
    return [ModelMessage(role="user", content=[TextBlock(text=text)])]


def record_template_hint_decision(
    *,
    session_id: UUID,
    template_hint: str,
    event_log: EventLog,
) -> None:
    """Emit a ``DECISION_INPUT`` so the audit log shows the picker click."""
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.USER,
        payload={
            "kind": "template_hint",
            "value": template_hint,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
        step=0,
    )


__all__ = [
    "CANCELLABLE_SESSION_STATUSES",
    "cancel_all_run_tasks",
    "cancel_session_workflow",
    "get_run_tasks",
    "has_active_run_task",
    "recover_all_orphaned_running_sessions",
    "recover_orphaned_running_session",
    "record_template_hint_decision",
    "spawn_flow_task",
]
