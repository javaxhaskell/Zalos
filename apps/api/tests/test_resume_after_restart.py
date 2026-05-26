"""Resume-after-server-restart test (final polish round).

Proves the resume guarantee that BP11's ResumeBanner depends on:
the session row + events.jsonl + manifest.json + workspace tree
all live on disk; a server restart (modeled by disposing the SQLAlchemy
engine + re-initializing it from the same DB path + spawning a fresh
TestClient against the same WORKSPACES_ROOT) returns a fully readable
session state.

This is what closes the "did you actually prove resume works" gap I
called out in the gap assessment. The session row, the event chain
(append-only, prev_event_id linked), and the manifest mirror are all
re-discoverable after the server process is recycled.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient


def test_session_state_survives_server_restart(
    test_db_url: str,
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create + populate a session, dispose the engine, reboot, re-read."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))

    import agentforge.config as config_module
    import agentforge.persistence.db as db_module

    config_module._settings = None
    db_module._engine = None
    db_module._session_factory = None

    from agentforge.persistence import models as _models  # noqa: F401
    from agentforge.persistence.db import (
        Base,
        dispose_engine,
        init_engine,
    )

    engine = init_engine(test_db_url)
    Base.metadata.create_all(engine)

    from agentforge.api.main import create_app

    # --- First server boot: create session, upload, partial run --------
    app_v1 = create_app()
    with TestClient(app_v1) as client_v1:
        create = client_v1.post("/sessions", json={"workflow": "author"})
        assert create.status_code == 201, create.text
        sid = create.json()["id"]
        workspace = Path(create.json()["workspace_path"])

        # Upload a sample so the event log has more than the lifespan events.
        upload = client_v1.post(
            f"/sessions/{sid}/files",
            files={
                "file": (
                    "x.csv",
                    io.BytesIO(b"a,b\n1,2\n3,4\n"),
                    "text/csv",
                )
            },
        )
        assert upload.status_code == 201, upload.text

        # Capture pre-restart state.
        before = client_v1.get(f"/sessions/{sid}").json()
        events_before = client_v1.get(f"/sessions/{sid}/events").json()["events"]
        assert before["status"] == "created"
        assert before["budget"]["file_count"] == 1
        assert len(events_before) >= 3  # workflow_started + workspace_allocated + file_uploaded

    # --- Simulate server restart: dispose engine, reset module state --
    dispose_engine()
    config_module._settings = None
    db_module._engine = None
    db_module._session_factory = None

    # The DB file + workspace dir + events.jsonl + manifest.json all
    # persist on disk. The new engine binds to the same paths.
    re_engine = init_engine(test_db_url)
    # No create_all this time — schema persists.
    assert re_engine is not None

    # --- Second server boot: re-read the same session ------------------
    app_v2 = create_app()
    with TestClient(app_v2) as client_v2:
        after = client_v2.get(f"/sessions/{sid}").json()
        assert after["status"] == before["status"]
        assert after["workspace_path"] == before["workspace_path"]
        assert after["budget"]["file_count"] == before["budget"]["file_count"]
        # last_event_id pinned in the row.
        assert after["last_event_id"] == before["last_event_id"]

        events_after = client_v2.get(f"/sessions/{sid}/events").json()["events"]
        # Same event count + same ids in the same order = chain intact.
        assert len(events_after) == len(events_before)
        for e_before, e_after in zip(events_before, events_after, strict=True):
            assert e_before["id"] == e_after["id"]
            assert e_before["kind"] == e_after["kind"]
            assert e_before["prev_event_id"] == e_after["prev_event_id"]

        # Audit export round-trips with a valid chain after restart.
        audit = client_v2.get(f"/audit/export/{sid}").json()
        assert audit["chain_check"]["valid"] is True
        assert audit["session_id"] == sid

        # Workspace on disk still has the upload.
        assert (workspace / "uploads" / "x.csv").is_file()


def test_paused_session_resume_signals_intact(
    test_db_url: str,
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session left at PAUSED_USER after the server bounces still
    surfaces the question_asked event on re-mount — that's what the
    wizard's ResumeBanner reads to render 'Welcome back. Picking up
    where you left off…'."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))

    import agentforge.config as config_module
    import agentforge.persistence.db as db_module

    config_module._settings = None
    db_module._engine = None
    db_module._session_factory = None

    from agentforge.persistence import models as _models  # noqa: F401
    from agentforge.persistence.db import (
        Base,
        dispose_engine,
        init_engine,
    )

    engine = init_engine(test_db_url)
    Base.metadata.create_all(engine)

    from agentforge.api.main import create_app
    from agentforge.persistence.event_log import EventLog
    from agentforge.persistence.workspace import WorkspaceManager
    from agentforge.schemas import ActorType, EventKind

    app_v1 = create_app()
    with TestClient(app_v1) as client_v1:
        create = client_v1.post("/sessions", json={"workflow": "author"})
        sid = create.json()["id"]
        workspace = Path(create.json()["workspace_path"])

        # Emit a QUESTION_ASKED + flip the row to paused_user to mimic
        # a mid-flow pause. (BP-final polish: the wizard's
        # ResumeBanner reads session.status to render its copy.)
        wm = WorkspaceManager(workspace.parent)
        log = EventLog(workspace_manager=wm)
        question = log.append(
            session_id=UUID(sid),
            kind=EventKind.QUESTION_ASKED,
            actor_type=ActorType.MODEL,
            payload={"question": "Which column has the merchant name?"},
            step=2,
        )
        from agentforge.persistence.db import get_session_factory
        from agentforge.persistence.models import SessionRow

        db = get_session_factory()()
        try:
            row = db.get(SessionRow, sid)
            assert row is not None
            row.status = "paused_user"
            db.commit()
        finally:
            db.close()

    # Restart the server.
    dispose_engine()
    config_module._settings = None
    db_module._engine = None
    db_module._session_factory = None
    init_engine(test_db_url)

    app_v2 = create_app()
    with TestClient(app_v2) as client_v2:
        after = client_v2.get(f"/sessions/{sid}").json()
        assert after["status"] == "paused_user"

        events = client_v2.get(f"/sessions/{sid}/events").json()["events"]
        kinds = [e["kind"] for e in events]
        assert "question_asked" in kinds

        # The wizard would POST /answer to resume; verify the endpoint
        # accepts after restart + links to the persisted question.
        answer = client_v2.post(
            f"/sessions/{sid}/answer",
            json={"answer": "the description column"},
        )
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["session_id"] == sid

        # The new event links back to the question persisted before restart.
        events_after = client_v2.get(f"/sessions/{sid}/events").json()["events"]
        answers = [e for e in events_after if e["kind"] == "answer_received"]
        assert len(answers) == 1
        assert answers[0]["payload"]["question_event_id"] == str(question.id)
