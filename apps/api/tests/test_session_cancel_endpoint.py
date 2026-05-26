"""HTTP integration tests for POST /sessions/{id}/cancel."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from agentforge.persistence.db import get_session_factory
from agentforge.persistence.models import SessionRow


def _create_session(client: TestClient, workflow: str = "author") -> dict:
    resp = client.post("/sessions", json={"workflow": workflow})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_row_status(session_id: str, status: str) -> None:
    db = get_session_factory()()
    try:
        row = db.get(SessionRow, session_id)
        assert row is not None
        row.status = status
        db.commit()
    finally:
        db.close()


def test_cancel_returns_404_for_unknown_session(app_client: TestClient) -> None:
    resp = app_client.post(f"/sessions/{uuid4()}/cancel")
    assert resp.status_code == 404


def test_cancel_returns_409_for_created_session(app_client: TestClient) -> None:
    session = _create_session(app_client)
    resp = app_client.post(f"/sessions/{session['id']}/cancel")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "session_not_cancellable"


def test_cancel_returns_409_for_completed_session(app_client: TestClient) -> None:
    session = _create_session(app_client)
    _set_row_status(session["id"], "completed")
    resp = app_client.post(f"/sessions/{session['id']}/cancel")
    assert resp.status_code == 409


def test_cancel_paused_user_session(app_client: TestClient) -> None:
    session = _create_session(app_client)
    sid = session["id"]
    _set_row_status(sid, "paused_user")

    resp = app_client.post(f"/sessions/{sid}/cancel")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed_other"
    assert body["terminal_error_code"] == "user_abandoned"

    events = app_client.get(f"/sessions/{sid}/events").json()["events"]
    failed = [e for e in events if e["kind"] == "workflow_failed"]
    assert any(e["payload"].get("error_code") == "user_abandoned" for e in failed)


def test_cancel_orphaned_running_session(app_client: TestClient) -> None:
    session = _create_session(app_client)
    sid = session["id"]
    _set_row_status(sid, "running")

    resp = app_client.post(f"/sessions/{sid}/cancel")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed_other"
    assert body["terminal_error_code"] == "user_abandoned"
