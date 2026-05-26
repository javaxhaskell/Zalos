"""Shared test fixtures for the AgentForge API."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

# Set test-mode env BEFORE importing the app so settings pick them up.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")

# Resolve fixture roots to the repo root so integration tests work regardless
# of pytest's invocation cwd. conftest.py lives at apps/api/tests/, three
# levels under the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("TEMPLATES_ROOT", str(_REPO_ROOT / "templates"))
os.environ.setdefault(
    "FIXTURES_BROKEN_AGENTS_ROOT",
    str(_REPO_ROOT / "fixtures" / "broken_agents"),
)


@pytest.fixture()
def workspaces_root(tmp_path: Path) -> Path:
    """Per-test workspaces dir."""
    root = tmp_path / "workspaces"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def _close_idle_asyncio_policy_loop() -> Iterator[None]:
    """Close pytest-asyncio's idle default loop when Python 3.13 creates one.

    pytest runs with ``filterwarnings = error``. On Python 3.13,
    pytest-asyncio can create a default event loop while swapping event-loop
    policies for async tests; if garbage collection happens during a later
    XLSX parse, that idle loop's self-pipe sockets surface as unraisable
    ``ResourceWarning`` failures. Closing only an already-registered,
    non-running loop keeps the warning policy strict without hiding real test
    failures.
    """
    yield
    policy = asyncio.get_event_loop_policy()
    local = getattr(policy, "_local", None)
    loop = getattr(local, "_loop", None)
    if loop is None or loop.is_closed() or loop.is_running():
        return
    loop.close()
    policy.set_event_loop(None)


@pytest.fixture()
def test_db_url(tmp_path: Path) -> str:
    """Per-test SQLite DB URL."""
    db_path = tmp_path / "test.db"
    return f"sqlite:///{db_path}"


@pytest.fixture()
def app_client(test_db_url: str, workspaces_root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A FastAPI TestClient with a fresh app per test (clean lifespan)."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    # Reset cached settings + engine
    import agentforge.config as config_module
    import agentforge.persistence.db as db_module

    config_module._settings = None
    db_module._engine = None
    db_module._session_factory = None

    # Load the ORM models module so its mapped classes register with
    # Base.metadata BEFORE create_all runs. Other test files import this
    # transitively (test_author_flow_e2e imports idempotency_store, etc.),
    # but running a single test file in isolation must also work.
    from agentforge.persistence import models as _models  # noqa: F401

    # Initialise schema (run migration head equivalent by creating all tables)
    from agentforge.persistence.db import Base, init_engine

    engine = init_engine(test_db_url)
    Base.metadata.create_all(engine)

    from agentforge.api.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Tool-handler fixtures (BP4)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tool_ctx_factory(
    workspaces_root: Path,
) -> Callable[[UUID | None], object]:
    """Build a fresh :class:`ToolContext` for a new allocated workspace.

    Returns a callable so tests can spin up multiple sessions if needed
    (e.g., to exercise path discipline across sessions).
    """
    from agentforge.config import get_settings
    from agentforge.persistence.event_log import EventLog
    from agentforge.persistence.workspace import WorkspaceManager
    from agentforge.schemas import Workflow
    from agentforge.tools import ToolContext

    wm = WorkspaceManager(root=workspaces_root)
    settings = get_settings()

    def _make(session_id: UUID | None = None) -> ToolContext:
        sid = session_id or uuid4()
        wm.allocate(sid, Workflow.AUTHOR)
        return ToolContext(
            session_id=sid,
            step=0,
            workspace_manager=wm,
            event_log=EventLog(wm),
            settings=settings,
        )

    return _make


@pytest.fixture()
def tool_ctx(tool_ctx_factory: Callable[[UUID | None], object]) -> object:
    """A single freshly-allocated :class:`ToolContext`."""
    return tool_ctx_factory(None)
