"""Unit tests for the phase-transition state machine (Build Prompt 5c).

The state machine is advisory at compile time, enforced at runtime —
every phase change goes through :func:`transition` so illegal
transitions raise :class:`WorkflowStateError` rather than corrupting
state. Tests cover the WORKFLOWS.md cross-cutting rules:

  * No transition into ``*_apply`` / ``*_applied`` without
    ``APPROVAL_GRANTED``.
  * No transition into ``*_finalise`` without ``ARTIFACT_GENERATED``.
  * No transition into ``repair_diagnose`` without
    ``REPRODUCTION_RESULT``.
  * Self-transition rejected.
  * Initial transition (``from_phase=None``) succeeds and emits
    ``PHASE_TRANSITIONED``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from agentforge.api.errors import WorkflowStateError
from agentforge.orchestrator import transition
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    AuthorPhase,
    EventKind,
    RepairPhase,
    Workflow,
)


@pytest.fixture()
def session_event_log(workspaces_root) -> tuple[Any, EventLog]:
    wm = WorkspaceManager(root=workspaces_root)
    sid = uuid4()
    wm.allocate(sid, Workflow.AUTHOR)
    return sid, EventLog(wm)


def test_initial_transition_emits_event(session_event_log) -> None:
    sid, event_log = session_event_log
    transition(
        session_id=sid,
        from_phase=None,
        to_phase=AuthorPhase.AUTHOR_TEMPLATE,
        event_log=event_log,
    )
    events = event_log.read_all(sid)
    phase_events = [e for e in events if e.kind == EventKind.PHASE_TRANSITIONED]
    assert len(phase_events) == 1
    payload = phase_events[0].payload
    assert payload["from_phase"] is None
    assert payload["to_phase"] == AuthorPhase.AUTHOR_TEMPLATE.value


def test_self_transition_rejected(session_event_log) -> None:
    sid, event_log = session_event_log
    with pytest.raises(WorkflowStateError, match="self-transition"):
        transition(
            session_id=sid,
            from_phase=AuthorPhase.AUTHOR_INFER,
            to_phase=AuthorPhase.AUTHOR_INFER,
            event_log=event_log,
        )


def test_apply_phase_requires_approval(session_event_log) -> None:
    sid, event_log = session_event_log
    with pytest.raises(WorkflowStateError, match="APPROVAL_GRANTED"):
        transition(
            session_id=sid,
            from_phase=AuthorPhase.AUTHOR_REVIEW_DIFF,
            to_phase=AuthorPhase.AUTHOR_APPLIED,
            event_log=event_log,
        )

    # After an APPROVAL_GRANTED event lands, the same transition succeeds.
    event_log.append(
        session_id=sid,
        kind=EventKind.APPROVAL_GRANTED,
        actor_type=ActorType.USER,
        payload={"tool_name": "write_file", "step": 1},
        step=1,
    )
    transition(
        session_id=sid,
        from_phase=AuthorPhase.AUTHOR_REVIEW_DIFF,
        to_phase=AuthorPhase.AUTHOR_APPLIED,
        event_log=event_log,
    )


def test_finalise_phase_requires_artifact_generated(session_event_log) -> None:
    sid, event_log = session_event_log
    with pytest.raises(WorkflowStateError, match="ARTIFACT_GENERATED"):
        transition(
            session_id=sid,
            from_phase=AuthorPhase.AUTHOR_REVIEW,
            to_phase=AuthorPhase.AUTHOR_FINALISE,
            event_log=event_log,
        )

    event_log.append(
        session_id=sid,
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={"path": "reports/validation_report.md"},
        step=2,
    )
    transition(
        session_id=sid,
        from_phase=AuthorPhase.AUTHOR_REVIEW,
        to_phase=AuthorPhase.AUTHOR_FINALISE,
        event_log=event_log,
    )


def test_repair_diagnose_requires_reproduction_result(
    session_event_log,
) -> None:
    sid, event_log = session_event_log
    with pytest.raises(WorkflowStateError, match="REPRODUCTION_RESULT"):
        transition(
            session_id=sid,
            from_phase=RepairPhase.REPAIR_REPRODUCE,
            to_phase=RepairPhase.REPAIR_DIAGNOSE,
            event_log=event_log,
        )

    event_log.append(
        session_id=sid,
        kind=EventKind.REPRODUCTION_RESULT,
        actor_type=ActorType.SYSTEM,
        payload={"reproduced": True},
        step=0,
    )
    transition(
        session_id=sid,
        from_phase=RepairPhase.REPAIR_REPRODUCE,
        to_phase=RepairPhase.REPAIR_DIAGNOSE,
        event_log=event_log,
    )
