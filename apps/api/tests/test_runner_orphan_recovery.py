"""Unit tests for orphaned RUNNING session recovery."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI

from agentforge.persistence.db import Base, get_session_factory, init_engine
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.models import SessionRow
from agentforge.persistence.session_store import SessionStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.orchestrator.runner import (
    get_run_tasks,
    recover_all_orphaned_running_sessions,
    recover_orphaned_running_session,
)
from agentforge.schemas import EventKind, SessionStatus, Workflow


@pytest.fixture()
def db_env(
    test_db_url: str,
    workspaces_root,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))
    import agentforge.config as config_module
    import agentforge.persistence.db as db_module

    config_module._settings = None
    db_module._engine = None
    db_module._session_factory = None
    from agentforge.persistence import models as _models  # noqa: F401

    engine = init_engine(test_db_url)
    Base.metadata.create_all(engine)
    yield
    db_module.dispose_engine()


@pytest.fixture()
def workspace_manager(workspaces_root, db_env) -> WorkspaceManager:
    return WorkspaceManager(root=workspaces_root)


def _create_running_row(
    *,
    workspace_manager: WorkspaceManager,
    db_factory,
) -> UUID:
    db = db_factory()
    try:
        store = SessionStore(
            db=db,
            workspace_manager=workspace_manager,
            event_log=EventLog(workspace_manager),
        )
        session = store.create_session(Workflow.AUTHOR)
        store.mark_running(session.id)
        return session.id
    finally:
        db.close()


def test_recover_orphaned_running_session_marks_failed_other(
    workspace_manager: WorkspaceManager,
) -> None:
    db_factory = get_session_factory()
    session_id = _create_running_row(
        workspace_manager=workspace_manager,
        db_factory=db_factory,
    )

    recovered = recover_orphaned_running_session(
        session_id=session_id,
        db_session_factory=db_factory,
        workspace_manager=workspace_manager,
    )
    assert recovered is not None
    assert recovered.status == SessionStatus.FAILED_OTHER
    assert recovered.terminal_error_code is not None
    assert recovered.terminal_error_code.value == "workflow_interrupted"

    event_log = EventLog(workspace_manager)
    events = event_log.read_all(session_id)
    assert any(event.kind == EventKind.WORKFLOW_FAILED for event in events)


def test_recover_orphan_preserves_prior_workflow_failed_error_code(
    workspace_manager: WorkspaceManager,
) -> None:
    from agentforge.schemas import ActorType, ErrorCode

    db_factory = get_session_factory()
    session_id = _create_running_row(
        workspace_manager=workspace_manager,
        db_factory=db_factory,
    )
    event_log = EventLog(workspace_manager)
    event_log.append(
        session_id=session_id,
        kind=EventKind.WORKFLOW_FAILED,
        actor_type=ActorType.SYSTEM,
        payload={
            "error_code": ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED.value,
            "message": "contract hash mismatch",
        },
        step=0,
    )

    recovered = recover_orphaned_running_session(
        session_id=session_id,
        db_session_factory=db_factory,
        workspace_manager=workspace_manager,
    )
    assert recovered is not None
    assert recovered.status == SessionStatus.FAILED_OTHER
    assert (
        recovered.terminal_error_code is not None
        and recovered.terminal_error_code.value
        == ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED.value
    )

    events = event_log.read_all(session_id)
    failed_events = [event for event in events if event.kind == EventKind.WORKFLOW_FAILED]
    assert len(failed_events) == 1


def test_recover_orphan_skips_when_active_task_registered(
    workspace_manager: WorkspaceManager,
) -> None:
    db_factory = get_session_factory()
    session_id = _create_running_row(
        workspace_manager=workspace_manager,
        db_factory=db_factory,
    )
    app = FastAPI()

    class _ActiveTask:
        def done(self) -> bool:
            return False

    get_run_tasks(app)[session_id] = _ActiveTask()  # type: ignore[assignment]

    recovered = recover_orphaned_running_session(
        session_id=session_id,
        app=app,
        db_session_factory=db_factory,
        workspace_manager=workspace_manager,
    )
    assert recovered is None


def test_recover_all_orphaned_running_sessions_on_startup(
    workspace_manager: WorkspaceManager,
) -> None:
    db_factory = get_session_factory()
    sid_one = _create_running_row(
        workspace_manager=workspace_manager,
        db_factory=db_factory,
    )
    sid_two = _create_running_row(
        workspace_manager=workspace_manager,
        db_factory=db_factory,
    )

    recovered = recover_all_orphaned_running_sessions(
        db_session_factory=db_factory,
        workspace_manager=workspace_manager,
    )
    assert recovered == 2

    db = db_factory()
    try:
        for sid in (sid_one, sid_two):
            row = db.get(SessionRow, str(sid))
            assert row is not None
            assert row.status == SessionStatus.FAILED_OTHER.value
    finally:
        db.close()


def test_recover_orphan_is_noop_for_unknown_session(
    workspace_manager: WorkspaceManager,
    db_env: None,
) -> None:
    recovered = recover_orphaned_running_session(
        session_id=uuid4(),
        db_session_factory=get_session_factory(),
        workspace_manager=workspace_manager,
    )
    assert recovered is None
