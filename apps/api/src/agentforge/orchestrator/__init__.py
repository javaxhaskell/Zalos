"""Workflow orchestrators — phase progression on top of the agent loop.

BP5c ships :class:`AuthorFlow` for the author workflow. BP6 adds
``RepairFlow``. Both share the state-machine guard in
:mod:`agentforge.orchestrator.state_machine`.
"""

from __future__ import annotations

from typing import Any

_LAZY_EXPORTS = {
    "AuthorFlow": ("agentforge.orchestrator.author_flow", "AuthorFlow"),
    "AuthorOutcome": ("agentforge.orchestrator.author_flow", "AuthorOutcome"),
    "RepairFlow": ("agentforge.orchestrator.repair_flow", "RepairFlow"),
    "RepairOutcome": ("agentforge.orchestrator.repair_flow", "RepairOutcome"),
    "cancel_all_run_tasks": ("agentforge.orchestrator.runner", "cancel_all_run_tasks"),
    "cancel_session_workflow": ("agentforge.orchestrator.runner", "cancel_session_workflow"),
    "CANCELLABLE_SESSION_STATUSES": (
        "agentforge.orchestrator.runner",
        "CANCELLABLE_SESSION_STATUSES",
    ),
    "get_run_tasks": ("agentforge.orchestrator.runner", "get_run_tasks"),
    "has_active_run_task": ("agentforge.orchestrator.runner", "has_active_run_task"),
    "recover_all_orphaned_running_sessions": (
        "agentforge.orchestrator.runner",
        "recover_all_orphaned_running_sessions",
    ),
    "recover_orphaned_running_session": (
        "agentforge.orchestrator.runner",
        "recover_orphaned_running_session",
    ),
    "record_template_hint_decision": (
        "agentforge.orchestrator.runner",
        "record_template_hint_decision",
    ),
    "spawn_flow_task": ("agentforge.orchestrator.runner", "spawn_flow_task"),
    "Phase": ("agentforge.orchestrator.state_machine", "Phase"),
    "transition": ("agentforge.orchestrator.state_machine", "transition"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _LAZY_EXPORTS[name]
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
