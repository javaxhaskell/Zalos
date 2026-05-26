"""Author blind evaluation integrity checks."""

from __future__ import annotations

import subprocess
from typing import Any
from uuid import UUID

from agentforge.orchestrator.author_llm_authoring import (
    model_contributed_to_authoring,
)
from agentforge.persistence.event_log import EventLog
from agentforge.schemas import ActorType, EventKind


def git_provenance() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
        dirty = True
    return {
        "git_commit": commit,
        "git_dirty": dirty,
        "author_requires_model_calls": True,
    }


def assert_blind_eval_completion_integrity(
    *,
    settings: Any,
    events: list[Any],
    workflow_type: str,
    build_mode: str,
    completion_via: str,
    model_calls: int,
) -> None:
    del workflow_type, build_mode, completion_via
    if not getattr(settings, "author_blind_eval_mode", False):
        return
    if model_calls <= 0 or not model_contributed_to_authoring(events):
        raise BlindEvalIntegrityError(
            "Blind eval: Author completion recorded without model-backed authoring stages."
        )


def record_blind_eval_metadata(
    *,
    session_id: UUID,
    event_log: EventLog,
    step: int,
    workflow_type: str,
    build_mode: str,
    completion_via: str,
    model_calls: int,
) -> None:
    event_log.append(
        session_id=session_id,
        kind=EventKind.DECISION_INPUT,
        actor_type=ActorType.SYSTEM,
        payload={
            "kind": "author_blind_eval_metadata",
            "workflow_type": workflow_type,
            "build_mode": build_mode,
            "completion_via": completion_via,
            "model_calls": model_calls,
            **git_provenance(),
        },
        step=step,
    )


class BlindEvalIntegrityError(RuntimeError):
    """Raised when blind evaluation integrity rules are violated."""
