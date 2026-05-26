"""FastAPI dependency providers.

Phase 1 exposed: session, settings, current_user.
Phase 3 adds: workspace_manager, event_log, session_store, artifact_store.

Each request gets a fresh DB session; the singletons (WorkspaceManager) are
shared. The store classes are thin and constructed per-request so they can
be substituted in tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from agentforge.config import Settings, get_settings
from agentforge.models import ModelClient
from agentforge.persistence.artifact_store import ArtifactStore
from agentforge.persistence.db import get_session_factory
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import IdempotencyStore
from agentforge.persistence.session_store import SessionStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.tools import ToolRegistry

# ---------------------------------------------------------------------
# Primitive resources
# ---------------------------------------------------------------------


def get_db_session() -> Iterator[Session]:
    """Yield a request-scoped SQLAlchemy session."""
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_settings_dep() -> Settings:
    return get_settings()


def get_current_user_id() -> str:
    """Return the demo user id (single-user prototype)."""
    return get_settings().demo_user_id


# ---------------------------------------------------------------------
# Workspace + event log (singletons keyed on the settings root path)
# ---------------------------------------------------------------------


@lru_cache(maxsize=1)
def _workspace_manager_for(root: str) -> WorkspaceManager:
    from pathlib import Path

    return WorkspaceManager(Path(root))


def get_workspace_manager(
    settings: Settings = Depends(get_settings_dep),
) -> WorkspaceManager:
    return _workspace_manager_for(str(settings.workspaces_root))


def reset_workspace_manager_cache() -> None:
    """Test hook: forget the cached WorkspaceManager so a new tmp root applies."""
    _workspace_manager_for.cache_clear()


def get_event_log(
    wm: WorkspaceManager = Depends(get_workspace_manager),
) -> EventLog:
    return EventLog(workspace_manager=wm)


# ---------------------------------------------------------------------
# Higher-level stores
# ---------------------------------------------------------------------


def get_session_store(
    db: Session = Depends(get_db_session),
    wm: WorkspaceManager = Depends(get_workspace_manager),
    event_log: EventLog = Depends(get_event_log),
) -> SessionStore:
    return SessionStore(db=db, workspace_manager=wm, event_log=event_log)


def get_artifact_store(
    db: Session = Depends(get_db_session),
    wm: WorkspaceManager = Depends(get_workspace_manager),
    event_log: EventLog = Depends(get_event_log),
) -> ArtifactStore:
    return ArtifactStore(db=db, workspace_manager=wm, event_log=event_log)


# ---------------------------------------------------------------------
# Tool registry (built once in lifespan; read-only thereafter)
# ---------------------------------------------------------------------


def get_tool_registry(request: Request) -> ToolRegistry:
    """Return the process-wide tool registry built at lifespan startup.

    The registry is the security boundary (INV-2). Reads only; no
    code path mutates the registry after the lifespan has yielded.
    """
    # ``app.state`` is dynamically typed (Any); cast back so mypy sees
    # the concrete return type. The lifespan guarantees this attribute
    # is set before any request handler runs.
    registry: ToolRegistry = request.app.state.tool_registry
    return registry


# ---------------------------------------------------------------------
# Idempotency store (per-request, since it shares the DB session)
# ---------------------------------------------------------------------


def get_idempotency_store(
    db: Session = Depends(get_db_session),
) -> IdempotencyStore:
    """Return a request-scoped :class:`IdempotencyStore` (INV-7)."""
    return IdempotencyStore(db=db)


# ---------------------------------------------------------------------
# Model client (built once in lifespan; reused per request)
# ---------------------------------------------------------------------


def get_model_client(request: Request) -> ModelClient:
    """Return the process-wide model client built at lifespan startup.

    Concrete type is :class:`AnthropicModelClient` when a real
    Anthropic API key is configured, otherwise :class:`FakeModelClient`
    with an empty script (any unscripted ``complete()`` call raises).
    Tests that drive the agent loop construct their own
    :class:`FakeModelClient` with a script and pass it in directly.
    """
    # ``app.state`` is dynamically typed (Any); cast back. Tests may
    # override this attribute mid-flight to inject a scripted client.
    client: ModelClient = request.app.state.model_client
    return client
