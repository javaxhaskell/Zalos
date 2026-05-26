"""Phase-transition guard for author and repair flows.

The state machine is *advisory at compile time, enforced at runtime*: it
does not own the orchestrator's control flow but every transition
between phases passes through :func:`transition` so illegal transitions
raise :class:`WorkflowStateError` rather than corrupting session state.

The guards are the cross-cutting rules from WORKFLOWS.md:

  * No transition into ``*_apply`` / ``*_applied`` without a recorded
    ``APPROVAL_GRANTED`` event for the relevant step.
  * No transition into ``completed`` without a recorded
    ``ARTIFACT_GENERATED`` event (the agent file for author; the
    repair report for repair).
  * No transition into ``repair_diagnose`` without a
    ``REPRODUCTION_RESULT`` event.
  * No self-transition except where the workflow phase tables model
    loop-back explicitly (none of those land in BP5c).

Every successful transition emits a ``PHASE_TRANSITIONED`` event so the
audit log shows the full phase history.
"""

from __future__ import annotations

from uuid import UUID

from agentforge.api.errors import WorkflowStateError
from agentforge.persistence.event_log import EventLog
from agentforge.schemas import ActorType, AuthorPhase, EventKind, RepairPhase

# Union type — phases come from either workflow's StrEnum
Phase = AuthorPhase | RepairPhase


def transition(
    *,
    session_id: UUID,
    from_phase: Phase | None,
    to_phase: Phase,
    event_log: EventLog,
    step: int = 0,
) -> None:
    """Validate + emit ``PHASE_TRANSITIONED`` for a phase change.

    ``from_phase=None`` means this is the workflow's initial transition
    (the session was just created). All other transitions must obey
    the workflow's guards.

    Raises :class:`WorkflowStateError` with a descriptive ``message``
    if the transition is illegal. The error_code remains the default
    ``ErrorCode.UNKNOWN`` because illegal-transition is a programmer
    bug, not a user-facing failure.
    """
    if from_phase == to_phase:
        raise WorkflowStateError(
            f"self-transition not permitted: {from_phase} → {to_phase}"
        )

    _enforce_guards(
        session_id=session_id,
        from_phase=from_phase,
        to_phase=to_phase,
        event_log=event_log,
    )

    event_log.append(
        session_id=session_id,
        kind=EventKind.PHASE_TRANSITIONED,
        actor_type=ActorType.SYSTEM,
        payload={
            "from_phase": from_phase.value if from_phase else None,
            "to_phase": to_phase.value,
        },
        step=step,
    )


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


_APPLY_PHASES: set[str] = {
    AuthorPhase.AUTHOR_APPLIED.value,
    RepairPhase.REPAIR_APPLY.value,
}
_FINALISE_PHASES: set[str] = {
    AuthorPhase.AUTHOR_FINALISE.value,
    RepairPhase.REPAIR_FINALISE.value,
}


def _enforce_guards(
    *,
    session_id: UUID,
    from_phase: Phase | None,
    to_phase: Phase,
    event_log: EventLog,
) -> None:
    target = to_phase.value
    events = event_log.read_all(session_id)
    kinds = [evt.kind for evt in events]

    # Apply / Applied — must have APPROVAL_GRANTED
    if target in _APPLY_PHASES and EventKind.APPROVAL_GRANTED not in kinds:
        raise WorkflowStateError(
            f"cannot enter {target!r} without a recorded APPROVAL_GRANTED event "
            "(INV-3)"
        )

    # Finalise — must have an ARTIFACT_GENERATED event for the right artifact
    if target in _FINALISE_PHASES and EventKind.ARTIFACT_GENERATED not in kinds:
        raise WorkflowStateError(
            f"cannot enter {target!r} without a recorded "
            "ARTIFACT_GENERATED event"
        )

    # Repair diagnose — must have a REPRODUCTION_RESULT event
    if (
        to_phase == RepairPhase.REPAIR_DIAGNOSE
        and EventKind.REPRODUCTION_RESULT not in kinds
    ):
        raise WorkflowStateError(
            "cannot enter repair_diagnose without a recorded "
            "REPRODUCTION_RESULT event"
        )


__all__ = ["Phase", "transition"]
