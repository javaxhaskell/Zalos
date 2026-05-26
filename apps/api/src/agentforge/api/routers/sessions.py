"""Session lifecycle endpoints.

Phase 3 lit up the read paths + create + upload. Phase 8 wires the
load-bearing run/answer/finalise endpoints so the wizard can drive the
orchestrator from a click.

  * POST /sessions: create row + allocate workspace + emit initial events.
  * GET /sessions: list sessions for the current user.
  * GET /sessions/{id}: fetch single session.
  * POST /sessions/{id}/run: dispatch AuthorFlow/RepairFlow on a
    background task. 202 Accepted on spawn; 404 if unknown; 409 if the
    session is already RUNNING with an active task or COMPLETED (INV-7
    single-run gate). Orphaned RUNNING rows (no active task) are
    recovered automatically and may be restarted.
  * POST /sessions/{id}/answer: record the user's response to a
    pending ``question_asked``. Emits ``answer_received`` so the loop
    can resume on the next /run.
  * POST /sessions/{id}/cancel: stop a running or paused workflow,
    mark the session ``failed_other`` with ``user_abandoned``, and
    cancel any active background task.
  * POST /sessions/{id}/finalise: HTTP-side equivalent of the
    ``finalise_session`` tool — refuses without an ARTIFACT_GENERATED
    event, updates manifest + DB row.
  * GET /sessions/{id}/events: chronological event log (polled by the
    frontend every 2 s; supports ?after=<event_id> for incremental fetches).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from agentforge.api.deps import (
    get_event_log,
    get_model_client,
    get_session_store,
    get_settings_dep,
    get_tool_registry,
    get_workspace_manager,
)
from agentforge.api.errors import APIError
from agentforge.config import Settings
from agentforge.models import ModelClient
from agentforge.orchestrator import (
    CANCELLABLE_SESSION_STATUSES,
    cancel_session_workflow,
    has_active_run_task,
    record_template_hint_decision,
    recover_orphaned_running_session,
    spawn_flow_task,
)
from agentforge.orchestrator.author_date_clarification import (
    process_pending_date_clarification_answer,
)
from agentforge.persistence.db import get_session_factory
from agentforge.fault_tolerance.user_mitigation import build_failure_mitigation
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.session_store import SessionNotFoundError, SessionStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    EventKind,
    Session,
    SessionArchiveResponse,
    SessionAnswerRequest,
    SessionAnswerResponse,
    SessionCancelResponse,
    SessionCreate,
    SessionEventsResponse,
    SessionFinaliseRequest,
    SessionFinaliseResponse,
    SessionList,
    SessionListView,
    SessionBudgetSummary,
    SessionRestoreResponse,
    SessionRunRequest,
    SessionRunResponse,
    SessionStatus,
)
from agentforge.tools import ToolRegistry

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _not_found(session_id: UUID) -> APIError:
    err = APIError(
        message=f"session {session_id} not found",
        session_id=session_id,
    )
    err.http_status = 404
    return err


def _session_conflict(
    session_id: UUID,
    current_status: SessionStatus,
    *,
    reason: str | None = None,
) -> HTTPException:
    """409 envelope when /run is called against a non-runnable session."""
    if reason == "active_run":
        message = (
            "This session is already running. Wait for the current run to "
            "finish, then try again if needed."
        )
    elif current_status == SessionStatus.COMPLETED:
        message = (
            "This session already finished successfully and cannot be rerun."
        )
    else:
        message = (
            f"session {session_id} cannot start a new run from status "
            f"{current_status.value!r}"
        )
    return HTTPException(
        status_code=409,
        detail={
            "error_code": "session_state_conflict",
            "message": message,
            "current_status": current_status.value,
            "reason": reason,
        },
    )


@router.post(
    "",
    response_model=Session,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    payload: SessionCreate,
    store: SessionStore = Depends(get_session_store),
) -> Session:
    """Create a new author or repair session."""
    return store.create_session(payload.workflow)


@router.get("", response_model=SessionList)
async def list_sessions(
    limit: int = Query(default=100, ge=1, le=500),
    view: SessionListView = Query(
        default=SessionListView.ACTIVE,
        description=(
            "Dashboard filter: active (default), archived, or recently "
            "deleted (last 30 days)."
        ),
    ),
    store: SessionStore = Depends(get_session_store),
) -> SessionList:
    """List sessions, newest-first."""
    return store.list_sessions(limit=limit, view=view)


@router.get("/budget/summary", response_model=SessionBudgetSummary)
async def get_budget_summary(
    recent_limit: int = Query(default=10, ge=0, le=100),
    store: SessionStore = Depends(get_session_store),
) -> SessionBudgetSummary:
    """Return lifetime token usage aggregated across all sessions."""
    return store.get_budget_summary(recent_limit=recent_limit)


@router.get("/{session_id}", response_model=Session)
async def get_session(
    session_id: Annotated[UUID, Path(description="Session UUID")],
    request: Request,
    store: SessionStore = Depends(get_session_store),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
    event_log: EventLog = Depends(get_event_log),
) -> Session:
    """Fetch session detail (status, phase, budgets, current step)."""
    try:
        session = store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc

    if session.status == SessionStatus.RUNNING and not has_active_run_task(
        request.app, session_id
    ):
        recovered = recover_orphaned_running_session(
            session_id=session_id,
            app=request.app,
            db_session_factory=get_session_factory(),
            workspace_manager=workspace_manager,
        )
        if recovered is not None:
            session = recovered

    mitigation = build_failure_mitigation(
        session_id=session_id,
        workflow=session.workflow,
        status=session.status,
        terminal_error_code=session.terminal_error_code,
        events=event_log.read_all(session_id),
        workspace=workspace_manager.path_for(session_id),
    )
    if mitigation is not None:
        return session.model_copy(update={"failure_mitigation": mitigation})
    return session


# ---------------------------------------------------------------------------
# Run (BP8) — kick off the orchestrator on a background task
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/run",
    response_model=SessionRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_session(
    session_id: Annotated[UUID, Path()],
    body: SessionRunRequest,
    request: Request,
    store: SessionStore = Depends(get_session_store),
    settings: Settings = Depends(get_settings_dep),
    model_client: ModelClient = Depends(get_model_client),
    registry: ToolRegistry = Depends(get_tool_registry),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
    event_log: EventLog = Depends(get_event_log),
) -> SessionRunResponse:
    """Dispatch the workflow flow on a background task.

    Idempotency gate (INV-7):
      * Unknown session → 404.
      * Status RUNNING with an active background task → 409.
      * Status RUNNING without an active task → recover as interrupted,
        then allow a fresh run.
      * Status COMPLETED → 409 (no re-spawn).

    Any other status (CREATED, PAUSED_*, FAILED_*) is permitted to
    (re)start a run. For the failed family the loop builds its own
    initial messages from the event log so prior progress shows up in
    the model's context.
    """
    try:
        session = store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc

    if session.status == SessionStatus.RUNNING:
        if has_active_run_task(request.app, session_id):
            raise _session_conflict(
                session_id,
                session.status,
                reason="active_run",
            )
        recovered = recover_orphaned_running_session(
            session_id=session_id,
            app=request.app,
            db_session_factory=get_session_factory(),
            workspace_manager=workspace_manager,
        )
        if recovered is not None:
            session = recovered
        else:
            session = store.get_session(session_id)
    elif session.status == SessionStatus.COMPLETED:
        raise _session_conflict(session_id, session.status)

    if body.template_hint:
        record_template_hint_decision(
            session_id=session_id,
            template_hint=body.template_hint,
            event_log=event_log,
        )

    running = store.mark_running(session_id)

    spawn_flow_task(
        app=request.app,
        session_id=session_id,
        workflow=session.workflow,
        user_message=body.user_message,
        template_hint=body.template_hint,
        settings=settings,
        model_client=model_client,
        registry=registry,
        workspace_manager=workspace_manager,
        db_session_factory=get_session_factory(),
    )

    return SessionRunResponse(
        session_id=session_id,
        status=running.status,
        started_at=running.updated_at,
    )


# ---------------------------------------------------------------------------
# Answer (BP8) — record the user's reply to a question_asked event
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/answer",
    response_model=SessionAnswerResponse,
)
async def submit_answer(
    session_id: Annotated[UUID, Path()],
    body: SessionAnswerRequest,
    store: SessionStore = Depends(get_session_store),
    event_log: EventLog = Depends(get_event_log),
) -> SessionAnswerResponse:
    """Record an ``answer_received`` event for a paused session.

    The agent loop currently emits ``question_asked`` via the
    ``ask_user`` tool (BP5a). This endpoint persists the reply; the
    next ``POST /sessions/{id}/run`` call resumes the loop with the
    answer visible in the event log.
    """
    try:
        session = store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc

    # Find the latest question_asked event that does not yet have a
    # matching answer_received — that's the one this answer addresses.
    events = event_log.read_all(session_id)
    answered_ids: set[str] = set()
    for evt in events:
        if evt.kind == EventKind.ANSWER_RECEIVED:
            answered = evt.payload.get("question_event_id")
            if isinstance(answered, str):
                answered_ids.add(answered)

    open_question = None
    for evt in reversed(events):
        if evt.kind != EventKind.QUESTION_ASKED:
            continue
        if str(evt.id) in answered_ids:
            continue
        open_question = evt
        break

    now = datetime.now(UTC)
    appended = event_log.append(
        session_id=session_id,
        kind=EventKind.ANSWER_RECEIVED,
        actor_type=ActorType.USER,
        payload={
            "answer": body.answer,
            "question_event_id": str(open_question.id) if open_question else None,
            "answered_at": now.isoformat(),
        },
        step=open_question.step if open_question else session.current_step,
        ts=now,
    )

    open_payload = open_question.payload if open_question is not None else {}
    if (
        open_question is not None
        and isinstance(open_payload, dict)
        and open_payload.get("clarification_kind") == "date_format"
    ):
        process_pending_date_clarification_answer(
            session_id=session_id,
            event_log=event_log,
            step=open_question.step,
        )

    return SessionAnswerResponse(
        session_id=session_id,
        event_id=appended.id,
        accepted_at=now,
    )


# ---------------------------------------------------------------------------
# Cancel (BP8) — stop an in-progress workflow
# ---------------------------------------------------------------------------


def _cancel_conflict(session_id: UUID, current_status: SessionStatus) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error_code": "session_not_cancellable",
            "message": (
                f"session {session_id} cannot be cancelled from status "
                f"{current_status.value!r}"
            ),
            "current_status": current_status.value,
        },
    )


@router.post(
    "/{session_id}/cancel",
    response_model=SessionCancelResponse,
)
async def cancel_session_endpoint(
    session_id: Annotated[UUID, Path()],
    request: Request,
    store: SessionStore = Depends(get_session_store),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
    event_log: EventLog = Depends(get_event_log),
) -> SessionCancelResponse:
    """Cancel a running or paused workflow.

    Stops any active background task, records ``USER_ABANDONED``, and
    marks the session ``failed_other``. Terminal sessions return 409.
    """
    try:
        session = store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc

    if session.status not in CANCELLABLE_SESSION_STATUSES:
        raise _cancel_conflict(session_id, session.status)

    events_before = len(event_log.read_all(session_id))
    try:
        cancelled = await cancel_session_workflow(
            app=request.app,
            session_id=session_id,
            db_session_factory=get_session_factory(),
            workspace_manager=workspace_manager,
        )
    except ValueError as exc:
        raise _cancel_conflict(session_id, session.status) from exc

    events_after = event_log.read_all(session_id)
    event_id = None
    if len(events_after) > events_before:
        last = events_after[-1]
        if last.kind == EventKind.WORKFLOW_FAILED:
            event_id = last.id

    return SessionCancelResponse(
        session_id=session_id,
        status=cancelled.status,
        terminal_error_code=(
            cancelled.terminal_error_code.value
            if cancelled.terminal_error_code is not None
            else None
        ),
        cancelled_at=cancelled.updated_at,
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# Finalise (BP8) — HTTP-side equivalent of the finalise_session tool
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/finalise",
    response_model=SessionFinaliseResponse,
)
async def finalise_session_endpoint(
    session_id: Annotated[UUID, Path()],
    body: SessionFinaliseRequest,
    store: SessionStore = Depends(get_session_store),
    event_log: EventLog = Depends(get_event_log),
) -> SessionFinaliseResponse:
    """Mark the session COMPLETED in both manifest and DB row.

    Refuses without an ``ARTIFACT_GENERATED`` event, mirroring the
    tool-side ``finalise_session`` guard so the HTTP path can never
    "complete" a session that produced no artifact (defends the
    promise in WORKFLOWS.md §1 step 12).
    """
    try:
        session = store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc

    if session.status == SessionStatus.COMPLETED:
        # Idempotent re-finalise: surface the existing completion
        # without producing a duplicate event.
        return SessionFinaliseResponse(
            session_id=session_id,
            status=SessionStatus.COMPLETED,
            finalised_at=session.completed_at or session.updated_at,
            event_id=session.last_event_id or session_id,
        )

    events = event_log.read_all(session_id)
    if not any(e.kind == EventKind.ARTIFACT_GENERATED for e in events):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "finalise_without_artifact",
                "message": (
                    "cannot finalise: no ARTIFACT_GENERATED event "
                    "on record (the agent must produce a validation "
                    "or repair report first)"
                ),
            },
        )

    now = datetime.now(UTC)
    completed = store.mark_terminal(session_id, SessionStatus.COMPLETED)

    payload: dict[str, str] = {
        "workflow": session.workflow.value,
        "via": "http_endpoint",
    }
    if body.summary:
        payload["summary"] = body.summary
    completion_event = event_log.append(
        session_id=session_id,
        kind=EventKind.WORKFLOW_COMPLETED,
        actor_type=ActorType.USER,
        payload=payload,
        step=session.current_step,
        ts=now,
    )

    return SessionFinaliseResponse(
        session_id=session_id,
        status=completed.status,
        finalised_at=completed.completed_at or now,
        event_id=completion_event.id,
    )


def _reject_running_session(session_id: UUID) -> HTTPException:
    """409 when archive/delete is attempted against a running session."""
    return HTTPException(
        status_code=409,
        detail={
            "error_code": "session_running",
            "message": (
                f"session {session_id} is still running and cannot be "
                "archived or deleted yet"
            ),
        },
    )


@router.post(
    "/{session_id}/archive",
    response_model=SessionArchiveResponse,
)
async def archive_session_endpoint(
    session_id: Annotated[UUID, Path()],
    store: SessionStore = Depends(get_session_store),
    event_log: EventLog = Depends(get_event_log),
) -> SessionArchiveResponse:
    """Archive a session so it no longer appears on the dashboard."""
    try:
        row = store.get_session_row_for_lifecycle(session_id)
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc

    if row.deleted_at is not None:
        raise _not_found(session_id)

    if row.status == SessionStatus.RUNNING.value:
        raise _reject_running_session(session_id)

    if row.status == SessionStatus.AUTO_ARCHIVED.value:
        return SessionArchiveResponse(
            session_id=session_id,
            status=SessionStatus.AUTO_ARCHIVED,
            archived_at=row.updated_at,
            event_id=UUID(row.last_event_id) if row.last_event_id else session_id,
        )

    now = datetime.now(UTC)
    archived = store.archive_session(session_id)
    archive_event = event_log.append(
        session_id=session_id,
        kind=EventKind.SESSION_AUTO_ARCHIVED,
        actor_type=ActorType.USER,
        payload={"via": "http_endpoint"},
        step=row.current_step,
        ts=now,
    )

    return SessionArchiveResponse(
        session_id=session_id,
        status=archived.status,
        archived_at=now,
        event_id=archive_event.id,
    )


@router.post(
    "/{session_id}/restore",
    response_model=SessionRestoreResponse,
)
async def restore_session_endpoint(
    session_id: Annotated[UUID, Path()],
    store: SessionStore = Depends(get_session_store),
) -> SessionRestoreResponse:
    """Restore an archived or recently deleted session to the active list."""
    try:
        row = store.get_session_row_for_lifecycle(session_id)
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc

    if row.status == SessionStatus.RUNNING.value:
        raise _reject_running_session(session_id)

    now = datetime.now(UTC)
    restored = store.restore_session(session_id)
    return SessionRestoreResponse(
        session_id=session_id,
        status=restored.status,
        restored_at=now,
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_session_endpoint(
    session_id: Annotated[UUID, Path()],
    permanent: bool = Query(
        default=False,
        description=(
            "When true, permanently removes the session and workspace. "
            "When false (default), moves the session to Recently deleted."
        ),
    ),
    store: SessionStore = Depends(get_session_store),
) -> None:
    """Remove a session from the dashboard (soft) or erase it permanently."""
    try:
        row = store.get_session_row_for_lifecycle(session_id)
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc

    if row.status == SessionStatus.RUNNING.value:
        raise _reject_running_session(session_id)

    if permanent:
        store.delete_session(session_id)
    else:
        store.soft_delete_session(session_id)


# ---------------------------------------------------------------------------
# Events polling
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/events",
    response_model=SessionEventsResponse,
)
async def list_session_events(
    session_id: Annotated[UUID, Path()],
    after: UUID | None = Query(
        default=None,
        description="Return only events strictly after this event id. Used by the frontend polling loop.",
    ),
    store: SessionStore = Depends(get_session_store),
    event_log: EventLog = Depends(get_event_log),
) -> SessionEventsResponse:
    """Return events for a session in chronological order.

    The frontend polls this every 2 seconds while a session is RUNNING.
    """
    try:
        store.get_session(session_id)  # raises if missing
    except SessionNotFoundError as exc:
        raise _not_found(session_id) from exc
    events = event_log.read_since(session_id, after)
    return SessionEventsResponse(
        session_id=session_id,
        events=events,
        has_more=False,
    )
