"""Audit endpoints.

Phase 3 lights up:
  - GET /audit/export/{session_id}: the full append-only event log + the
    session manifest, with a light chain-integrity check, so a downstream
    auditor can reconstruct the session without consulting other sources.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from agentforge.api.deps import (
    get_event_log,
    get_session_store,
    get_workspace_manager,
)
from agentforge.api.errors import APIError
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.session_store import SessionNotFoundError, SessionStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import AuditExportResponse, ChainCheck

router = APIRouter(prefix="/audit", tags=["audit"])


def _session_not_found(session_id: UUID) -> APIError:
    err = APIError(
        message=f"session {session_id} not found",
        session_id=session_id,
    )
    err.http_status = status.HTTP_404_NOT_FOUND
    return err


@router.get(
    "/export/{session_id}",
    response_model=AuditExportResponse,
)
async def export_audit(
    session_id: Annotated[UUID, ...],
    store: SessionStore = Depends(get_session_store),
    event_log: EventLog = Depends(get_event_log),
    wm: WorkspaceManager = Depends(get_workspace_manager),
) -> AuditExportResponse:
    """Return the full event log + manifest for a session, JSON-serialised.

    The response is self-contained: a downstream auditor can reconstruct
    "what happened" from this payload alone (subject to the prototype's
    append-only-by-convention contract; production hardens this with a
    storage-level CHECK + integrity-hash chain — see ADR-0004).
    """
    try:
        store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise _session_not_found(session_id) from exc

    manifest = wm.read_manifest(session_id)
    events = event_log.read_all(session_id)
    chain_valid, chain_msg = event_log.verify_chain(session_id)

    return AuditExportResponse(
        session_id=session_id,
        exported_at=datetime.now(UTC),
        manifest=manifest,
        events=events,
        event_count=len(events),
        chain_check=ChainCheck(valid=chain_valid, message=chain_msg),
    )
