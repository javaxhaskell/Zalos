"""Approval endpoints (BP5b).

The agent loop pauses on a write tool with ``requires_approval=True``
by emitting an ``APPROVAL_REQUESTED`` event (payload carries
``request_id``, ``tool_name``, and the ``step``). These two endpoints
record the user's grant or decline:

  * Emit ``APPROVAL_GRANTED`` / ``APPROVAL_DECLINED`` with the same
    ``step`` so the loop's :meth:`AgentLoop._has_approval` returns true
    on the next ``run`` call (resume).
  * Lazily UPSERT the ``ApprovalRequestRow`` + insert an
    ``ApprovalDecisionRow`` so admin queries can list pending /
    decided approvals without scanning the event log.

The endpoints are intentionally idempotent: re-POSTing the same
``request_id`` returns the existing decision rather than appending a
duplicate event. This is the right ergonomic for the UI (which may
retry on a flaky network) and matches INV-7's spirit even though
approvals are not subject to the idempotency cache.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from agentforge.api.deps import get_db_session, get_event_log
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.models import (
    ApprovalDecisionRow,
    ApprovalRequestRow,
    SessionRow,
)
from agentforge.schemas import (
    ActorType,
    ApprovalActionResponse,
    ApprovalDeclineRequest,
    ApprovalGrantRequest,
    ApprovalStatus,
    EventKind,
)

router = APIRouter(prefix="/sessions/{session_id}", tags=["approvals"])


@router.post(
    "/approve",
    response_model=ApprovalActionResponse,
    status_code=200,
)
async def approve(
    session_id: Annotated[UUID, Path()],
    body: ApprovalGrantRequest,
    db: Session = Depends(get_db_session),
    event_log: EventLog = Depends(get_event_log),
) -> ApprovalActionResponse:
    """Grant approval for a pending request.

    The ``request_id`` in the body must correspond to an
    ``APPROVAL_REQUESTED`` event in the session's log. The matching
    request's ``step`` is copied onto the granted event so the agent
    loop's per-step approval gate (INV-3) sees the decision on resume.
    """
    pending = _find_pending(
        session_id=session_id,
        request_id=body.request_id,
        event_log=event_log,
        db=db,
    )
    return _record_decision(
        session_id=session_id,
        pending=pending,
        new_status=ApprovalStatus.GRANTED,
        decided_by=body.decided_by,
        reason=None,
        kind=EventKind.APPROVAL_GRANTED,
        db=db,
        event_log=event_log,
    )


@router.post(
    "/reject",
    response_model=ApprovalActionResponse,
    status_code=200,
)
async def reject(
    session_id: Annotated[UUID, Path()],
    body: ApprovalDeclineRequest,
    db: Session = Depends(get_db_session),
    event_log: EventLog = Depends(get_event_log),
) -> ApprovalActionResponse:
    """Decline a pending approval. ``reason`` is required (ADR-0006).

    A decline transitions the loop to bounded decline-cycle behaviour
    (per ADR-0006, max 2 iterations before terminal ``failed_user_reject``).
    BP5b records the event; BP5c's orchestrator implements the cycle
    accounting.
    """
    pending = _find_pending(
        session_id=session_id,
        request_id=body.request_id,
        event_log=event_log,
        db=db,
    )
    return _record_decision(
        session_id=session_id,
        pending=pending,
        new_status=ApprovalStatus.DECLINED,
        decided_by=body.decided_by,
        reason=body.reason,
        kind=EventKind.APPROVAL_DECLINED,
        db=db,
        event_log=event_log,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _find_pending(
    *,
    session_id: UUID,
    request_id: UUID,
    event_log: EventLog,
    db: Session,
) -> _PendingApproval:
    """Return the pending approval's step + tool name, or raise 404/409.

    Looks at the event log first (authoritative for INV-6 — events are
    append-only and we never trust the SQL mirror for state).
    """
    # 404 if the session doesn't exist at all.
    session_row = db.get(SessionRow, str(session_id))
    if session_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "session_not_found",
                "message": f"session {session_id} not found",
            },
        )

    events = event_log.read_all(session_id)
    request_event = None
    for evt in events:
        if (
            evt.kind == EventKind.APPROVAL_REQUESTED
            and evt.payload.get("request_id") == str(request_id)
        ):
            request_event = evt
            break

    if request_event is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "approval_request_not_found",
                "message": f"no APPROVAL_REQUESTED event with request_id {request_id}",
            },
        )

    # 409 if a decision already exists.
    for evt in events:
        if (
            evt.kind in (EventKind.APPROVAL_GRANTED, EventKind.APPROVAL_DECLINED)
            and evt.payload.get("request_id") == str(request_id)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "approval_already_decided",
                    "message": (
                        f"request_id {request_id} already decided "
                        f"({evt.kind.value})"
                    ),
                },
            )

    return _PendingApproval(
        request_id=request_id,
        step=int(request_event.payload.get("step", request_event.step)),
        tool_name=str(request_event.payload.get("tool_name", "unknown")),
    )


def _record_decision(
    *,
    session_id: UUID,
    pending: _PendingApproval,
    new_status: ApprovalStatus,
    decided_by: str,
    reason: str | None,
    kind: EventKind,
    db: Session,
    event_log: EventLog,
) -> ApprovalActionResponse:
    """Persist + emit the decision; return the action response."""
    now = datetime.now(UTC)

    # UPSERT the ApprovalRequestRow if it wasn't pre-persisted.
    request_row = db.get(ApprovalRequestRow, str(pending.request_id))
    if request_row is None:
        request_row = ApprovalRequestRow(
            id=str(pending.request_id),
            session_id=str(session_id),
            step=pending.step,
            kind=pending.tool_name,
            business_summary=f"approval for tool {pending.tool_name}",
            diff_paths_json=json.dumps([]),
            status=new_status.value,
            created_at=now,
        )
        db.add(request_row)
    else:
        request_row.status = new_status.value

    decision_row = ApprovalDecisionRow(
        id=str(uuid4()),
        request_id=str(pending.request_id),
        status=new_status.value,
        reason=reason,
        decided_at=now,
        decided_by=decided_by,
    )
    db.add(decision_row)
    db.flush()

    event = event_log.append(
        session_id=session_id,
        kind=kind,
        actor_type=ActorType.USER,
        payload={
            "request_id": str(pending.request_id),
            "tool_name": pending.tool_name,
            "step": pending.step,
            "status": new_status.value,
            "decided_by": decided_by,
            "reason": reason,
        },
        step=pending.step,
        ts=now,
    )
    db.commit()

    return ApprovalActionResponse(
        session_id=session_id,
        request_id=pending.request_id,
        status=new_status,
        decision_id=UUID(decision_row.id),
        event_id=event.id,
        decided_at=now,
    )


class _PendingApproval:
    """Internal carrier for the pending-approval details."""

    __slots__ = ("request_id", "step", "tool_name")

    def __init__(self, *, request_id: UUID, step: int, tool_name: str) -> None:
        self.request_id = request_id
        self.step = step
        self.tool_name = tool_name


