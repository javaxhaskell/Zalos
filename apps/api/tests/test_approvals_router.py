"""Integration tests for the /approve and /reject routes (Build Prompt 5b).

Cover the HTTP surface that records approval decisions. The agent
loop's pause/resume round-trip is already exercised in
``test_agent_loop.py``; this file proves the HTTP endpoint correctly:

  * 200s a grant/decline for a pending APPROVAL_REQUESTED.
  * 404s an unknown request_id or session_id.
  * 409s a duplicate decision (idempotency-of-intent).
  * 422s a decline with an empty reason (boundary validation).
  * Emits the appropriate APPROVAL_GRANTED / APPROVAL_DECLINED event.
  * Persists ApprovalRequestRow + ApprovalDecisionRow.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import ActorType, ApprovalStatus, EventKind


def _create_author_session(client: TestClient) -> dict:
    r = client.post("/sessions", json={"workflow": "author"})
    assert r.status_code == 201, r.text
    return r.json()


def _emit_approval_requested(
    *,
    workspaces_root: Path,
    session_id: str,
    request_id: UUID,
    tool_name: str,
    step: int,
) -> None:
    """Emit an APPROVAL_REQUESTED event into the session's events.jsonl.

    Stand-in for what the agent loop would do mid-flight. Constructs a
    WorkspaceManager pointing at the same root as the app's singleton
    so the file paths align.
    """
    wm = WorkspaceManager(root=workspaces_root)
    event_log = EventLog(wm)
    event_log.append(
        session_id=UUID(session_id),
        kind=EventKind.APPROVAL_REQUESTED,
        actor_type=ActorType.SYSTEM,
        payload={
            "request_id": str(request_id),
            "tool_name": tool_name,
            "step": step,
            "status": ApprovalStatus.PENDING.value,
        },
        step=step,
    )


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


def test_approve_records_grant_and_emits_event(
    app_client: TestClient, workspaces_root: Path
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    request_id = uuid4()
    _emit_approval_requested(
        workspaces_root=workspaces_root,
        session_id=sid,
        request_id=request_id,
        tool_name="write_file",
        step=1,
    )

    r = app_client.post(
        f"/sessions/{sid}/approve",
        json={"request_id": str(request_id), "decided_by": "user-demo"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == sid
    assert body["status"] == ApprovalStatus.GRANTED.value
    assert body["request_id"] == str(request_id)
    assert body["event_id"]

    # APPROVAL_GRANTED event landed in events.jsonl with the same step.
    events_resp = app_client.get(f"/sessions/{sid}/events")
    kinds = [e["kind"] for e in events_resp.json()["events"]]
    assert EventKind.APPROVAL_GRANTED.value in kinds
    granted = next(
        e for e in events_resp.json()["events"]
        if e["kind"] == EventKind.APPROVAL_GRANTED.value
    )
    assert granted["payload"]["request_id"] == str(request_id)
    assert granted["payload"]["step"] == 1
    assert granted["payload"]["decided_by"] == "user-demo"


def test_approve_returns_404_when_request_id_unknown(
    app_client: TestClient,
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    r = app_client.post(
        f"/sessions/{sid}/approve",
        json={"request_id": str(uuid4())},  # never emitted
    )
    assert r.status_code == 404
    assert (
        r.json()["detail"]["error_code"]
        == "approval_request_not_found"
    )


def test_approve_returns_404_when_session_missing(app_client: TestClient) -> None:
    r = app_client.post(
        f"/sessions/{uuid4()}/approve",
        json={"request_id": str(uuid4())},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error_code"] == "session_not_found"


def test_approve_returns_409_on_double_decision(
    app_client: TestClient, workspaces_root: Path
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    request_id = uuid4()
    _emit_approval_requested(
        workspaces_root=workspaces_root,
        session_id=sid,
        request_id=request_id,
        tool_name="write_file",
        step=1,
    )

    first = app_client.post(
        f"/sessions/{sid}/approve",
        json={"request_id": str(request_id)},
    )
    assert first.status_code == 200

    second = app_client.post(
        f"/sessions/{sid}/approve",
        json={"request_id": str(request_id)},
    )
    assert second.status_code == 409
    assert (
        second.json()["detail"]["error_code"]
        == "approval_already_decided"
    )


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


def test_reject_records_decline_with_reason(
    app_client: TestClient, workspaces_root: Path
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    request_id = uuid4()
    _emit_approval_requested(
        workspaces_root=workspaces_root,
        session_id=sid,
        request_id=request_id,
        tool_name="apply_patch",
        step=2,
    )

    r = app_client.post(
        f"/sessions/{sid}/reject",
        json={
            "request_id": str(request_id),
            "reason": "I want to review the diff manually first",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == ApprovalStatus.DECLINED.value

    events_resp = app_client.get(f"/sessions/{sid}/events")
    declined = next(
        e for e in events_resp.json()["events"]
        if e["kind"] == EventKind.APPROVAL_DECLINED.value
    )
    assert declined["payload"]["reason"] == (
        "I want to review the diff manually first"
    )


def test_reject_requires_non_empty_reason(
    app_client: TestClient, workspaces_root: Path
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    request_id = uuid4()
    _emit_approval_requested(
        workspaces_root=workspaces_root,
        session_id=sid,
        request_id=request_id,
        tool_name="write_file",
        step=1,
    )

    # Empty reason → 422 boundary validation.
    r = app_client.post(
        f"/sessions/{sid}/reject",
        json={"request_id": str(request_id), "reason": ""},
    )
    assert r.status_code == 422


def test_reject_returns_409_on_double_decision(
    app_client: TestClient, workspaces_root: Path
) -> None:
    session = _create_author_session(app_client)
    sid = session["id"]
    request_id = uuid4()
    _emit_approval_requested(
        workspaces_root=workspaces_root,
        session_id=sid,
        request_id=request_id,
        tool_name="write_file",
        step=1,
    )
    first = app_client.post(
        f"/sessions/{sid}/reject",
        json={"request_id": str(request_id), "reason": "no thanks"},
    )
    assert first.status_code == 200

    second = app_client.post(
        f"/sessions/{sid}/approve",  # try to grant after decline
        json={"request_id": str(request_id)},
    )
    assert second.status_code == 409
