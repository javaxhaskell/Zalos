"""Archive, soft-delete, restore, and permanent delete session endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from agentforge.persistence.db import get_session_factory
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.models import SessionRow
from agentforge.persistence.session_store import SessionStore
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import SessionStatus


def _create_author_session(client: TestClient) -> dict:
    response = client.post("/sessions", json={"workflow": "author"})
    assert response.status_code == 201, response.text
    return response.json()


def _mark_running(session: dict) -> None:
    factory = get_session_factory()
    db = factory()
    try:
        wm = WorkspaceManager(Path(session["workspace_path"]).parent)
        log = EventLog(workspace_manager=wm)
        store = SessionStore(db=db, workspace_manager=wm, event_log=log)
        store.mark_running(UUID(session["id"]))
    finally:
        db.close()


def test_archive_session_hides_from_list_and_emits_event(
    app_client: TestClient,
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    resp = app_client.post(f"/sessions/{sid}/archive")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == sid
    assert body["status"] == "auto_archived"

    listing = app_client.get("/sessions").json()["sessions"]
    assert sid not in [s["id"] for s in listing]

    archived_list = app_client.get("/sessions", params={"view": "archived"}).json()[
        "sessions"
    ]
    assert sid in [s["id"] for s in archived_list]

    detail = app_client.get(f"/sessions/{sid}").json()
    assert detail["status"] == "auto_archived"

    events = [
        json.loads(line)
        for line in (workspace / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert any(e["kind"] == "session_auto_archived" for e in events)


def test_archive_is_idempotent(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]

    first = app_client.post(f"/sessions/{sid}/archive")
    second = app_client.post(f"/sessions/{sid}/archive")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "auto_archived"


def test_soft_delete_moves_to_deleted_view(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])
    assert workspace.is_dir()

    resp = app_client.delete(f"/sessions/{sid}")
    assert resp.status_code == 204

    assert app_client.get(f"/sessions/{sid}").status_code == 404
    assert sid not in [s["id"] for s in app_client.get("/sessions").json()["sessions"]]

    deleted = app_client.get("/sessions", params={"view": "deleted"}).json()[
        "sessions"
    ]
    assert sid in [s["id"] for s in deleted]
    assert deleted[0]["deleted_at"] is not None
    assert workspace.exists()


def test_permanent_delete_removes_row_and_workspace(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    resp = app_client.delete(f"/sessions/{sid}", params={"permanent": True})
    assert resp.status_code == 204

    assert app_client.get(f"/sessions/{sid}").status_code == 404
    assert not workspace.exists()
    assert sid not in [
        s["id"]
        for s in app_client.get("/sessions", params={"view": "deleted"}).json()[
            "sessions"
        ]
    ]


def test_restore_archived_session(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]

    app_client.post(f"/sessions/{sid}/archive")
    resp = app_client.post(f"/sessions/{sid}/restore")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "created"

    active = app_client.get("/sessions").json()["sessions"]
    assert sid in [s["id"] for s in active]
    assert sid not in [
        s["id"]
        for s in app_client.get("/sessions", params={"view": "archived"}).json()[
            "sessions"
        ]
    ]


def test_restore_soft_deleted_session(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]

    app_client.delete(f"/sessions/{sid}")
    resp = app_client.post(f"/sessions/{sid}/restore")
    assert resp.status_code == 200, resp.text

    assert app_client.get(f"/sessions/{sid}").status_code == 200
    assert sid in [s["id"] for s in app_client.get("/sessions").json()["sessions"]]


def test_deleted_view_excludes_sessions_older_than_retention(
    app_client: TestClient,
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    app_client.delete(f"/sessions/{sid}")

    factory = get_session_factory()
    db = factory()
    try:
        row = db.get(SessionRow, sid)
        assert row is not None
        row.deleted_at = datetime.now(UTC) - timedelta(days=31)
        db.commit()
    finally:
        db.close()

    deleted = app_client.get("/sessions", params={"view": "deleted"}).json()[
        "sessions"
    ]
    assert sid not in [s["id"] for s in deleted]


def test_archive_rejects_running_session(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    _mark_running(session)

    resp = app_client.post(f"/sessions/{sid}/archive")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "session_running"


def test_delete_rejects_running_session(app_client: TestClient) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    _mark_running(session)

    resp = app_client.delete(f"/sessions/{sid}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "session_running"


def test_archive_unknown_session_404(app_client: TestClient) -> None:
    missing = uuid4()
    resp = app_client.post(f"/sessions/{missing}/archive")
    assert resp.status_code == 404


def test_delete_unknown_session_404(app_client: TestClient) -> None:
    missing = uuid4()
    resp = app_client.delete(f"/sessions/{missing}")
    assert resp.status_code == 404
