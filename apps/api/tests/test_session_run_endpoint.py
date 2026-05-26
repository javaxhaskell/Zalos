"""HTTP integration tests for the BP8 wizard endpoints.

Covers:

  * POST /sessions/{id}/run happy path — wizard kicks off the author
    flow against a scripted FakeModelClient, polls events until
    WORKFLOW_COMPLETED, asserts LLM-authored artifacts exist, and confirms both manifest.status and the DB
    row's status are ``completed`` (the load-bearing verification gate).
  * POST /sessions/{id}/run idempotency — 404 unknown / 409 RUNNING /
    409 COMPLETED (INV-7).
  * POST /sessions/{id}/answer — records ``answer_received`` and links
    to the open ``question_asked`` event.
  * POST /sessions/{id}/finalise — refuses without ARTIFACT_GENERATED,
    succeeds once it's recorded, is idempotent on repeat.

The fake-client script is the same 7-turn flow exercised in-process by
``test_author_flow_e2e.py``. BP8's role is to prove the HTTP layer
dispatches it correctly, NOT to re-validate the orchestrator.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from agentforge.models import FakeModelClient, ModelResponse
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    ErrorCode,
    EventKind,
    SessionStatus,
)
from tests.author_model_fixtures import model_authoring_responses

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BANK_DIR = _REPO_ROOT / "templates" / "bank_categoriser"


# ---------------------------------------------------------------------------
# Shared fixtures (scoped to this file — the lifecycle suite already has its
# own conftest hooks).
# ---------------------------------------------------------------------------


def _scripted_responses() -> list[ModelResponse]:
    return model_authoring_responses(
        template_root=_BANK_DIR,
        workflow_type="bank_transaction_categorisation",
    )


def _create_session(client: TestClient, workflow: str = "author") -> dict:
    resp = client.post("/sessions", json={"workflow": workflow})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload_bank_sample(client: TestClient, session_id: str) -> dict:
    """POST the bank_categoriser sample CSV through the wizard's upload endpoint."""
    sample = (_BANK_DIR / "data" / "sample_input.csv").read_bytes()
    resp = client.post(
        f"/sessions/{session_id}/files",
        files={"file": ("sample_input.csv", io.BytesIO(sample), "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _poll_until(
    client: TestClient,
    session_id: str,
    predicate,
    *,
    timeout_s: float = 60.0,
    interval_s: float = 0.25,
) -> list[dict]:
    """Poll /events until ``predicate(events)`` is true. Returns the events.

    ``time.sleep`` on the test thread yields to the TestClient's
    underlying event loop so the background flow task can advance.
    """
    deadline = time.monotonic() + timeout_s
    events: list[dict] = []
    while time.monotonic() < deadline:
        resp = client.get(f"/sessions/{session_id}/events")
        assert resp.status_code == 200, resp.text
        events = resp.json()["events"]
        if predicate(events):
            return events
        time.sleep(interval_s)
    raise AssertionError(
        f"timed out after {timeout_s}s; last event kinds: "
        f"{[e['kind'] for e in events]}"
    )


# ---------------------------------------------------------------------------
# Idempotency / boundary cases
# ---------------------------------------------------------------------------


def test_run_returns_404_for_unknown_session(app_client: TestClient) -> None:
    resp = app_client.post(
        "/sessions/00000000-0000-0000-0000-000000000000/run",
        json={},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("error_code") in ("unknown", "session_not_found")


def test_run_recovers_orphaned_running_session(app_client: TestClient) -> None:
    """A RUNNING row with no active background task can start a fresh run."""
    from agentforge.persistence.db import get_session_factory
    from agentforge.persistence.models import SessionRow

    session = _create_session(app_client)
    sid = session["id"]

    factory = get_session_factory()
    db = factory()
    try:
        row = db.get(SessionRow, sid)
        assert row is not None
        row.status = "running"
        db.commit()
    finally:
        db.close()

    second = app_client.post(f"/sessions/{sid}/run", json={})
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "running"


def test_run_returns_409_when_active_task_running(app_client: TestClient) -> None:
    """A /run against a session with a live background task stays rejected."""
    import asyncio

    from agentforge.models import FakeModelClient
    from agentforge.orchestrator.runner import get_run_tasks

    class HangModelClient(FakeModelClient):
        async def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

    session = _create_session(app_client)
    sid = session["id"]
    app_client.app.state.model_client = HangModelClient(script=[])

    first = app_client.post(f"/sessions/{sid}/run", json={})
    assert first.status_code == 202, first.text

    second = app_client.post(f"/sessions/{sid}/run", json={})
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["current_status"] == "running"

    for task in list(get_run_tasks(app_client.app).values()):
        task.cancel()


def test_run_returns_409_when_already_completed(app_client: TestClient) -> None:
    """A run started after a successful /finalise is rejected with 409."""
    session = _create_session(app_client)
    sid = session["id"]

    # Bypass the flow: emit an ARTIFACT_GENERATED + finalise via the HTTP
    # endpoint. This puts the row in COMPLETED.
    workspace = Path(session["workspace_path"])
    wm = WorkspaceManager(workspace.parent)
    log = EventLog(workspace_manager=wm)
    log.append(
        session_id=UUID(sid),
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={"artifact_type": "validation_report", "path": "reports/x.md"},
        step=0,
    )
    final = app_client.post(f"/sessions/{sid}/finalise", json={})
    assert final.status_code == 200, final.text

    run = app_client.post(f"/sessions/{sid}/run", json={})
    assert run.status_code == 409
    assert run.json()["detail"]["current_status"] == "completed"


def test_run_accepts_failed_other_provenance_session(app_client: TestClient) -> None:
    """Terminal provenance failures are retryable via POST /run."""
    from agentforge.persistence.db import get_session_factory
    from agentforge.persistence.models import SessionRow
    from agentforge.schemas import ErrorCode

    session = _create_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])
    wm = WorkspaceManager(workspace.parent)
    log = EventLog(workspace_manager=wm)
    log.append(
        session_id=UUID(sid),
        kind=EventKind.WORKFLOW_FAILED,
        actor_type=ActorType.SYSTEM,
        payload={
            "error_code": ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED.value,
            "message": "contract hash mismatch",
        },
        step=0,
    )

    factory = get_session_factory()
    db = factory()
    try:
        row = db.get(SessionRow, sid)
        assert row is not None
        row.status = SessionStatus.FAILED_OTHER.value
        row.terminal_error_code = ErrorCode.AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED.value
        db.commit()
    finally:
        db.close()

    run = app_client.post(f"/sessions/{sid}/run", json={})
    assert run.status_code == 202, run.text
    assert run.json()["status"] == "running"

    refreshed = app_client.get(f"/sessions/{sid}")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "running"
    assert refreshed.json()["terminal_error_code"] is None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_drives_bank_categoriser_through_http(app_client: TestClient) -> None:
    """The load-bearing BP8 verification: HTTP /run → completed."""
    session = _create_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    # Upload the bank_categoriser sample through the wizard's endpoint.
    _upload_bank_sample(app_client, sid)

    # Wire up the scripted FakeModelClient.
    app_client.app.state.model_client = FakeModelClient(script=_scripted_responses())

    # POST /run — 202 Accepted.
    run = app_client.post(
        f"/sessions/{sid}/run",
        json={
            "user_message": (
                "Categorise the uploaded bank transactions using the "
                "bank_categoriser template's rule cascade."
            ),
            "template_hint": "bank_categoriser",
        },
    )
    assert run.status_code == 202, run.text
    body = run.json()
    assert body["session_id"] == sid
    assert body["status"] == "running"

    # Poll /events until WORKFLOW_COMPLETED appears.
    completed_events = _poll_until(
        app_client,
        sid,
        predicate=lambda evs: any(e["kind"] == "workflow_completed" for e in evs),
        timeout_s=90.0,
        interval_s=0.3,
    )
    kinds = [e["kind"] for e in completed_events]
    assert "template_seeded" not in kinds
    assert "validation_run" in kinds
    assert "artifact_generated" in kinds
    assert "decision_input" in kinds  # template_hint recorded

    assert (workspace / "generated" / "model_contract_plan.json").is_file()
    assert (workspace / "generated" / "model_contract_review.json").is_file()
    assert (workspace / "generated" / "agent.py").is_file()
    assert (workspace / "generated" / "tests" / "test_agent.py").is_file()
    assert (workspace / "outputs" / "output.csv").is_file()

    # System validation report says PASS; workflow report is separate when required.
    system_report = (workspace / "reports" / "system_validation_report.md").read_text()
    assert "Overall: PASS" in system_report

    # The DB row's status flipped to completed (BP8's new piece).
    refreshed = app_client.get(f"/sessions/{sid}")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "completed"
    assert refreshed.json()["completed_at"] is not None

    # And the manifest matches.
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    completion = manifest.get("completion") or {}
    assert completion.get("model_call_count", 0) >= 4
    assert completion.get("model_contributed") is True


# ---------------------------------------------------------------------------
# /answer
# ---------------------------------------------------------------------------


def test_answer_records_answer_received_event(app_client: TestClient) -> None:
    session = _create_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])

    # Seed an open question via the EventLog.
    wm = WorkspaceManager(workspace.parent)
    log = EventLog(workspace_manager=wm)
    question = log.append(
        session_id=UUID(sid),
        kind=EventKind.QUESTION_ASKED,
        actor_type=ActorType.MODEL,
        payload={"question": "What's the primary key column?"},
        step=3,
    )

    resp = app_client.post(
        f"/sessions/{sid}/answer",
        json={"answer": "txn_id is the primary key."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == sid
    assert UUID(body["event_id"]) != question.id  # a new event

    # The new event should be the latest answer_received and link to the question.
    events = app_client.get(f"/sessions/{sid}/events").json()["events"]
    answers = [e for e in events if e["kind"] == "answer_received"]
    assert len(answers) == 1
    assert answers[0]["payload"]["question_event_id"] == str(question.id)
    assert answers[0]["payload"]["answer"] == "txn_id is the primary key."


def test_answer_rejects_empty_string(app_client: TestClient) -> None:
    session = _create_session(app_client)
    resp = app_client.post(
        f"/sessions/{session['id']}/answer",
        json={"answer": ""},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /finalise
# ---------------------------------------------------------------------------


def test_finalise_refuses_without_artifact(app_client: TestClient) -> None:
    session = _create_session(app_client)
    resp = app_client.post(
        f"/sessions/{session['id']}/finalise",
        json={"summary": "premature"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "finalise_without_artifact"


def test_finalise_marks_completed_after_artifact(app_client: TestClient) -> None:
    session = _create_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])
    wm = WorkspaceManager(workspace.parent)
    log = EventLog(workspace_manager=wm)
    log.append(
        session_id=UUID(sid),
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={
            "artifact_type": "validation_report",
            "path": "reports/validation_report.md",
        },
        step=5,
    )

    resp = app_client.post(
        f"/sessions/{sid}/finalise",
        json={"summary": "done"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    # Row + manifest both reflect completed.
    refreshed = app_client.get(f"/sessions/{sid}").json()
    assert refreshed["status"] == "completed"
    assert refreshed["completed_at"] is not None
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["status"] == "completed"


def test_finalise_is_idempotent_when_completed(app_client: TestClient) -> None:
    session = _create_session(app_client)
    sid = session["id"]
    workspace = Path(session["workspace_path"])
    wm = WorkspaceManager(workspace.parent)
    log = EventLog(workspace_manager=wm)
    log.append(
        session_id=UUID(sid),
        kind=EventKind.ARTIFACT_GENERATED,
        actor_type=ActorType.SYSTEM,
        payload={"artifact_type": "validation_report"},
        step=5,
    )
    first = app_client.post(f"/sessions/{sid}/finalise", json={})
    assert first.status_code == 200
    second = app_client.post(f"/sessions/{sid}/finalise", json={})
    assert second.status_code == 200
    assert second.json()["status"] == "completed"


def test_finalise_404_for_unknown_session(app_client: TestClient) -> None:
    resp = app_client.post(
        f"/sessions/{uuid4()}/finalise",
        json={},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("path", ["answer", "finalise"])
def test_404_for_unknown_session_on_action(app_client: TestClient, path: str) -> None:
    payload = {"answer": "x"} if path == "answer" else {}
    resp = app_client.post(f"/sessions/{uuid4()}/{path}", json=payload)
    assert resp.status_code == 404
