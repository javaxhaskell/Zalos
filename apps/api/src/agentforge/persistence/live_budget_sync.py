"""Persist live session budget counters while a workflow is running.

The flow runner registers the active :class:`SessionStore` in a
contextvar so model-call sites can flush event-derived usage to the
``sessions`` row (and manifest mirror) without threading the store
through the entire orchestrator call graph.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from agentforge.persistence.event_log import EventLog
    from agentforge.persistence.session_store import SessionStore

_live_session_store: contextvars.ContextVar[SessionStore | None] = contextvars.ContextVar(
    "live_session_store",
    default=None,
)


def set_live_budget_session_store(store: SessionStore | None) -> contextvars.Token:
    """Register *store* for the current async task / thread."""
    return _live_session_store.set(store)


def reset_live_budget_session_store(token: contextvars.Token) -> None:
    _live_session_store.reset(token)


def persist_live_session_budget(
    *,
    session_id: UUID,
    event_log: EventLog,
) -> None:
    """Flush cumulative budget counters from the event log to the session row."""
    store = _live_session_store.get()
    if store is None:
        return
    from agentforge.persistence.user_facing import budget_status_from_events

    events = event_log.read_all(session_id)
    budget = budget_status_from_events(events)
    store.update_budget(
        session_id,
        tokens_used=budget.tokens_used,
        tool_calls_used=budget.tool_calls_used,
        steps_used=budget.steps_used,
        wall_seconds_used=budget.wall_seconds_used,
    )


__all__ = [
    "persist_live_session_budget",
    "reset_live_budget_session_store",
    "set_live_budget_session_store",
]
