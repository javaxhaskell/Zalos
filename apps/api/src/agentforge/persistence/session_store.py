"""Session CRUD + lifecycle orchestration.

The session row in SQLite is the authoritative truth for status, phase,
and budgets. The workspace dir on disk holds the events log, the manifest
(which has the same status/phase as a verifiable mirror), uploaded files,
and downstream artifacts.

This module owns the *creation* path (allocate row + workspace + initial
events) and the *read* path (single session + list). Mutations beyond
creation land in later prompts (state-machine transitions in Prompt 5).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from agentforge.persistence.event_log import EventLog
from agentforge.persistence.models import SessionRow
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    BudgetStatus,
    ErrorCode,
    EventKind,
    ResumeManifest,
    Session,
    SessionBudgetBreakdownItem,
    SessionBudgetSummary,
    SessionList,
    SessionListItem,
    SessionListView,
    SessionStatus,
    Workflow,
)

DELETED_SESSION_RETENTION_DAYS = 30


class SessionNotFoundError(Exception):
    """Raised when a requested session does not exist."""

    def __init__(self, session_id: UUID) -> None:
        super().__init__(f"session not found: {session_id}")
        self.session_id = session_id


class SessionStore:
    """Read/write sessions across SQLite + the per-session workspace."""

    def __init__(
        self,
        db: DBSession,
        workspace_manager: WorkspaceManager,
        event_log: EventLog,
    ) -> None:
        self.db = db
        self.wm = workspace_manager
        self.event_log = event_log

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create_session(self, workflow: Workflow) -> Session:
        """Create a new session.

        - Allocates a per-session workspace dir (with manifest.json).
        - Inserts the ``sessions`` row.
        - Emits ``workflow_started`` + ``workspace_allocated`` events.
        """
        session_id = uuid4()
        now = datetime.now(UTC)

        workspace_path = self.wm.allocate(
            session_id=session_id,
            workflow=workflow,
            started_at=now,
        )

        row = SessionRow(
            id=str(session_id),
            workflow=workflow.value,
            status=SessionStatus.CREATED.value,
            current_phase=None,
            current_step=0,
            started_at=now,
            updated_at=now,
            workspace_path=str(workspace_path),
            manifest_schema_version=1,
            tokens_used=0,
            tool_calls_used=0,
            steps_used=0,
            wall_seconds_used=0,
            file_count=0,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        # Initial events. These are the load-bearing first records of the
        # session — any resume path uses them as the chronological anchor.
        started_evt = self.event_log.append(
            session_id=session_id,
            kind=EventKind.WORKFLOW_STARTED,
            actor_type=ActorType.SYSTEM,
            payload={"workflow": workflow.value},
            step=0,
            ts=now,
        )
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.WORKSPACE_ALLOCATED,
            actor_type=ActorType.SYSTEM,
            payload={"workspace_path": str(workspace_path)},
            step=0,
        )

        # Record the head event so resume can skip the lookup.
        row.last_event_id = str(started_evt.id)
        self.db.commit()

        return self._row_to_pydantic(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_session_row_for_lifecycle(self, session_id: UUID) -> SessionRow:
        """Load a session row for archive/delete/restore (includes soft-deleted)."""
        row = self.db.get(SessionRow, str(session_id))
        if row is None:
            raise SessionNotFoundError(session_id)
        return row

    def get_session(self, session_id: UUID) -> Session:
        row = self.get_session_row_for_lifecycle(session_id)
        if row.deleted_at is not None:
            raise SessionNotFoundError(session_id)
        return self._row_to_pydantic(row)

    def list_sessions(
        self,
        limit: int = 100,
        *,
        view: SessionListView = SessionListView.ACTIVE,
    ) -> SessionList:
        query = self.db.query(SessionRow)
        if view == SessionListView.ACTIVE:
            query = query.filter(
                SessionRow.deleted_at.is_(None),
                SessionRow.status != SessionStatus.AUTO_ARCHIVED.value,
            )
        elif view == SessionListView.ARCHIVED:
            query = query.filter(
                SessionRow.deleted_at.is_(None),
                SessionRow.status == SessionStatus.AUTO_ARCHIVED.value,
            )
        elif view == SessionListView.DELETED:
            cutoff = datetime.now(UTC) - timedelta(days=DELETED_SESSION_RETENTION_DAYS)
            query = query.filter(
                SessionRow.deleted_at.isnot(None),
                SessionRow.deleted_at >= cutoff,
            )
        if view == SessionListView.DELETED:
            query = query.order_by(SessionRow.deleted_at.desc())
        else:
            query = query.order_by(SessionRow.started_at.desc())
        rows = query.limit(limit).all()
        return SessionList(
            sessions=[
                SessionListItem(
                    id=UUID(r.id),
                    workflow=Workflow(r.workflow),
                    status=SessionStatus(r.status),
                    current_phase=r.current_phase,
                    started_at=r.started_at,
                    updated_at=r.updated_at,
                    deleted_at=r.deleted_at,
                )
                for r in rows
            ]
        )

    def get_budget_summary(self, *, recent_limit: int = 10) -> SessionBudgetSummary:
        """Aggregate token usage across all non-soft-deleted sessions."""
        active_rows = self.db.query(SessionRow).filter(SessionRow.deleted_at.is_(None))
        session_count = active_rows.count()
        total = (
            self.db.query(func.coalesce(func.sum(SessionRow.tokens_used), 0))
            .filter(SessionRow.deleted_at.is_(None))
            .scalar()
        )
        recent_rows = (
            active_rows.order_by(SessionRow.started_at.desc())
            .limit(recent_limit)
            .all()
        )
        return SessionBudgetSummary(
            lifetime_tokens_used=int(total or 0),
            session_count=session_count,
            recent_sessions=[
                SessionBudgetBreakdownItem(
                    id=UUID(row.id),
                    workflow=Workflow(row.workflow),
                    tokens_used=row.tokens_used,
                    status=SessionStatus(row.status),
                )
                for row in recent_rows
            ],
        )

    # ------------------------------------------------------------------
    # Resume verification (read-only)
    # ------------------------------------------------------------------

    def resume_check(self, session_id: UUID) -> tuple[Session, ResumeManifest, list[str]]:
        """Check the session is resumable: row + manifest + file hashes.

        Returns ``(session, manifest, warnings)``. Warnings is a list of
        human-readable strings describing any drift between the DB row,
        the manifest, or the on-disk file hashes.
        """
        session = self.get_session(session_id)
        manifest = self.wm.read_manifest(session_id)
        warnings: list[str] = []

        if manifest.status != session.status:
            warnings.append(
                f"manifest.status={manifest.status.value} but DB row.status={session.status.value}"
            )
        if manifest.workflow != session.workflow:
            warnings.append(
                f"manifest.workflow={manifest.workflow.value} but DB row.workflow={session.workflow.value}"
            )
        intact, drifts = self.wm.verify_uploads_against_manifest(session_id)
        if not intact:
            for path, msg in drifts.items():
                warnings.append(f"upload {path}: {msg}")
        return session, manifest, warnings

    # ------------------------------------------------------------------
    # Mutation (minimal — fuller state-machine arrives in Prompt 5)
    # ------------------------------------------------------------------

    def record_uploaded_file(self, session_id: UUID) -> None:
        """Increment the file_count counter after a successful upload."""
        row = self.db.get(SessionRow, str(session_id))
        if row is None:
            raise SessionNotFoundError(session_id)
        row.file_count += 1
        row.updated_at = datetime.now(UTC)
        self.db.commit()

    # ------------------------------------------------------------------
    # Run lifecycle (BP8)
    # ------------------------------------------------------------------

    def mark_running(self, session_id: UUID) -> Session:
        """Flip the session row to ``running`` ahead of background dispatch.

        Mirrors the manifest update from ``WorkspaceManager.update_manifest``
        so the row and the on-disk truth do not drift. Raises
        :class:`SessionNotFoundError` if the row is missing.
        """
        row = self.db.get(SessionRow, str(session_id))
        if row is None:
            raise SessionNotFoundError(session_id)
        now = datetime.now(UTC)
        row.status = SessionStatus.RUNNING.value
        row.terminal_error_code = None
        row.completed_at = None
        row.updated_at = now
        self.db.commit()
        self.db.refresh(row)
        self.wm.update_manifest(session_id, status=SessionStatus.RUNNING.value)
        return self._row_to_pydantic(row)

    def update_budget(
        self,
        session_id: UUID,
        *,
        tokens_used: int | None = None,
        tool_calls_used: int | None = None,
        steps_used: int | None = None,
        wall_seconds_used: int | None = None,
    ) -> Session:
        """Persist accumulated budget counters to the row (BP11).

        Called from the runner at flow termination so a
        :class:`Session` row read post-run carries the final usage
        numbers. The wizard's :class:`BudgetBanner` reads these via
        ``GET /sessions/{id}`` for any session that's no longer
        polling events (e.g., a re-opened completed session). For
        live in-flight tracking the wizard derives the counters from
        the event stream directly, so this method is the persisted-
        historical-record path, not the live-update path.

        Each counter is opt-in — pass ``None`` to leave the column
        unchanged so partial updates are safe.
        """
        row = self.db.get(SessionRow, str(session_id))
        if row is None:
            raise SessionNotFoundError(session_id)
        if tokens_used is not None:
            row.tokens_used = tokens_used
        if tool_calls_used is not None:
            row.tool_calls_used = tool_calls_used
        if steps_used is not None:
            row.steps_used = steps_used
        if wall_seconds_used is not None:
            row.wall_seconds_used = wall_seconds_used
        row.updated_at = datetime.now(UTC)
        self.db.commit()
        self.db.refresh(row)
        # Mirror counters into the manifest so the workspace snapshot
        # is honest (used by the audit export + the wizard's recent-
        # session cards which read manifest.json for finalised runs).
        budget_payload = BudgetStatus(
            tokens_used=row.tokens_used,
            tool_calls_used=row.tool_calls_used,
            steps_used=row.steps_used,
            wall_seconds_used=row.wall_seconds_used,
            file_count=row.file_count,
        )
        self.wm.update_manifest(
            session_id, budget=budget_payload.model_dump(mode="json")
        )
        return self._row_to_pydantic(row)

    def mark_terminal(
        self,
        session_id: UUID,
        status: SessionStatus,
        terminal_error_code: ErrorCode | None = None,
    ) -> Session:
        """Persist the flow's terminal status to the row and manifest.

        Called from the background task that owns the
        :class:`AuthorFlow.run` / :class:`RepairFlow.run` invocation.
        ``completed_at`` is set when the status is COMPLETED; for the
        FAILED_* family it stays ``None`` (the row records the failure
        but the session is not "completed").
        """
        row = self.db.get(SessionRow, str(session_id))
        if row is None:
            raise SessionNotFoundError(session_id)
        now = datetime.now(UTC)
        row.status = status.value
        row.updated_at = now
        if status == SessionStatus.COMPLETED:
            row.completed_at = now
        if terminal_error_code is not None:
            row.terminal_error_code = terminal_error_code.value
        self.db.commit()
        self.db.refresh(row)
        self.wm.update_manifest(session_id, status=status.value)
        return self._row_to_pydantic(row)

    def archive_session(self, session_id: UUID) -> Session:
        """Mark a session ``auto_archived`` so it drops off the dashboard list."""
        row = self.db.get(SessionRow, str(session_id))
        if row is None or row.deleted_at is not None:
            raise SessionNotFoundError(session_id)
        if row.status == SessionStatus.AUTO_ARCHIVED.value:
            return self._row_to_pydantic(row)
        now = datetime.now(UTC)
        row.status_before_archive = row.status
        row.status = SessionStatus.AUTO_ARCHIVED.value
        row.updated_at = now
        self.db.commit()
        self.db.refresh(row)
        self.wm.update_manifest(session_id, status=SessionStatus.AUTO_ARCHIVED.value)
        return self._row_to_pydantic(row)

    def soft_delete_session(self, session_id: UUID) -> Session:
        """Hide a session on the Recently deleted tab (retained 30 days)."""
        row = self.db.get(SessionRow, str(session_id))
        if row is None:
            raise SessionNotFoundError(session_id)
        if row.deleted_at is not None:
            return self._row_to_pydantic(row)
        now = datetime.now(UTC)
        row.deleted_at = now
        row.updated_at = now
        self.db.commit()
        self.db.refresh(row)
        return self._row_to_pydantic(row)

    def restore_session(self, session_id: UUID) -> Session:
        """Undo soft-delete or unarchive back to the active dashboard list."""
        row = self.db.get(SessionRow, str(session_id))
        if row is None:
            raise SessionNotFoundError(session_id)
        now = datetime.now(UTC)
        if row.deleted_at is not None:
            row.deleted_at = None
            row.updated_at = now
            self.db.commit()
            self.db.refresh(row)
            if row.status != SessionStatus.AUTO_ARCHIVED.value:
                self.wm.update_manifest(session_id, status=row.status)
            return self._row_to_pydantic(row)

        if row.status == SessionStatus.AUTO_ARCHIVED.value:
            prior = row.status_before_archive
            if prior is None:
                prior = (
                    SessionStatus.COMPLETED.value
                    if row.completed_at is not None
                    else SessionStatus.CREATED.value
                )
            row.status = prior
            row.status_before_archive = None
            row.updated_at = now
            self.db.commit()
            self.db.refresh(row)
            self.wm.update_manifest(session_id, status=prior)
            return self._row_to_pydantic(row)

        return self._row_to_pydantic(row)

    def delete_session(self, session_id: UUID) -> None:
        """Hard-delete the session row (cascades related rows) and workspace."""
        row = self.db.get(SessionRow, str(session_id))
        if row is None:
            raise SessionNotFoundError(session_id)
        self.db.delete(row)
        self.db.commit()
        ws = self.wm.path_for(session_id)
        if ws.is_dir():
            shutil.rmtree(ws, ignore_errors=True)

    # ------------------------------------------------------------------
    # ORM ↔ Pydantic
    # ------------------------------------------------------------------

    def _row_to_pydantic(self, row: SessionRow) -> Session:
        return Session(
            id=UUID(row.id),
            workflow=Workflow(row.workflow),
            status=SessionStatus(row.status),
            current_phase=row.current_phase,
            current_step=row.current_step,
            started_at=row.started_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
            workspace_path=row.workspace_path,
            manifest_schema_version=row.manifest_schema_version,
            budget=BudgetStatus(
                tokens_used=row.tokens_used,
                tool_calls_used=row.tool_calls_used,
                steps_used=row.steps_used,
                wall_seconds_used=row.wall_seconds_used,
                file_count=row.file_count,
            ),
            last_event_id=UUID(row.last_event_id) if row.last_event_id else None,
            terminal_error_code=(
                ErrorCode(row.terminal_error_code)
                if row.terminal_error_code is not None
                else None
            ),
        )
