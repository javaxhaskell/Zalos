"""Shared infrastructure for tool handlers.

Every tool handler receives a ``ToolContext`` so the registry interface is
uniform across read tools, write tools, and execution tools. The context
carries the session-scoped collaborators a handler needs to enforce INV-5
(path discipline via :class:`WorkspaceManager`) and INV-6 (audit emission
via :class:`EventLog`).

BP5a adds ``settings`` for handlers that need cross-cutting paths
(``templates_root`` for reference material, ``subprocess_*`` for the
execution tools that land in BP5b). The idempotency store and budget
tracker land here in BP5b/c.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from agentforge.config import Settings
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager


@dataclass
class ToolContext:
    """Session-scoped collaborators handed to every tool handler.

    ``ToolContext`` is intentionally a plain dataclass (not a Pydantic
    model): it carries live objects (``WorkspaceManager``, ``EventLog``,
    ``Settings``), not boundary data. Pydantic's ``extra='forbid'`` would
    reject these fields, and INV-8 only governs module boundaries —
    internal call plumbing is exempt.
    """

    session_id: UUID
    step: int
    workspace_manager: WorkspaceManager
    event_log: EventLog
    settings: Settings
